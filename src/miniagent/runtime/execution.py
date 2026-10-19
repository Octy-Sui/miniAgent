from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactRef
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    ExecutionIdentity,
    ExecutionObserver,
    ManagedExecutionError,
)
from miniagent.evaluation import CandidateOutput
from miniagent.metering.usage import RequestCost, add_request_usage
from miniagent.models.messages import ToolMessage
from miniagent.models.protocol import ModelResponse
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)
from miniagent.runtime.context import CompactionPlan
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalRequested,
    ApprovalResolved,
    CompactionAttempt,
    CompactionAttemptRecorded,
    CompactionFailed,
    CompactionRequested,
    EvaluationCompleted,
    EvaluationStart,
    ModelRequested,
    ModelResponded,
    OperationExecutionEnded,
    OperationExecutionIdentified,
    PolicyDecided,
    RunFinished,
    RunStarted,
    StepCompleted,
    StepStarted,
    TerminationDecided,
    ToolEnded,
    ToolExecutionEnded,
    ToolExecutionStarted,
    ToolStarted,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    CurrentStep,
    ExecutionPhase,
    OperationRef,
    RequestProgress,
    ResponseDisposition,
    TerminateDisposition,
    TerminationDecision,
)
from miniagent.runtime.result import RunError, RunResult, TerminationReason
from miniagent.runtime.state import ConversationState, RunCursor, RunState, RunStatus
from miniagent.runtime.termination import tool_step_failed
from miniagent.runtime.trace_context import RunTraceContext, ToolCorrelation
from miniagent.runtime.transitions import (
    accept_model_response,
    prepare,
    start_running,
    wait_for_approval,
)
from miniagent.runtime.transitions import (
    begin_evaluation as transition_to_evaluation,
)
from miniagent.runtime.transitions import (
    begin_step as advance_step,
)
from miniagent.runtime.transitions import (
    begin_tool_execution as count_tool_execution,
)
from miniagent.runtime.transitions import (
    complete_tool as append_tool_result,
)
from miniagent.runtime.transitions import (
    complete_tool_step as advance_tool_step,
)
from miniagent.runtime.transitions import (
    finish as finish_state,
)
from miniagent.runtime.transitions import (
    resolve_approval as transition_from_approval,
)
from miniagent.tools.contracts import ToolResult
from miniagent.tools.rendering import render_tool_result


@dataclass(frozen=True, slots=True)
class RunTermination:
    status: RunStatus
    reason: TerminationReason
    error: RunError | None = None


@dataclass(frozen=True, slots=True)
class ContinueRun:
    pass


@dataclass(frozen=True, slots=True)
class EvaluateCandidate:
    candidate_output: CandidateOutput


@dataclass(frozen=True, slots=True)
class TerminateRun:
    termination: RunTermination


RunOutcome = ContinueRun | EvaluateCandidate | TerminateRun


@dataclass(slots=True)
class _PendingApprovalResolution:
    resolution: ApprovalResolution


@dataclass(slots=True)
class _PendingEvaluationResult:
    passed: bool
    final_output: str | None
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(slots=True)
class RunExecution:
    state: RunState
    journal: RunJournal
    trace: RunTraceContext
    _active_mark: float = field(default_factory=monotonic)
    pending_approval_resolution: _PendingApprovalResolution | None = None
    pending_evaluation_result: _PendingEvaluationResult | None = None

    @classmethod
    async def start(
        cls,
        state: RunState,
        journal: RunJournal,
    ) -> "RunExecution":
        execution = cls(
            state=state,
            journal=journal,
            trace=RunTraceContext.create(state.identity.run_id),
        )
        await execution._start()
        return execution

    @classmethod
    def attach(cls, state: RunState, journal: RunJournal) -> "RunExecution":
        trace = RunTraceContext.create(state.identity.run_id)
        if state.execution.current_step is not None:
            trace.begin_step(state.execution.current_step.step_number)
            operation = state.execution.active_operation
            if operation is not None and operation.kind == "model":
                trace.begin_model()
        return cls(state=state, journal=journal, trace=trace)

    def account_active_time(self) -> None:
        now = monotonic()
        elapsed_ms = int((now - self._active_mark) * 1000)
        self._active_mark += elapsed_ms / 1000
        self.state = self.state.model_copy(
            update={
                "usage": self.state.usage.model_copy(
                    update={
                        "active_duration_ms": self.state.usage.active_duration_ms
                        + elapsed_ms
                    }
                )
            }
        )

    async def record_termination(self, decision: TerminationDecision) -> None:
        self.account_active_time()
        state = self.state.model_copy(
            update={
                "execution": self.state.execution.model_copy(
                    update={
                        "phase": ExecutionPhase.TERMINATE,
                        "response_disposition": TerminateDisposition(decision=decision),
                        "active_operation": None,
                        "tool_batch": None,
                        "cursor": self.state.execution.cursor
                        if self.state.lifecycle.status is not RunStatus.RUNNING
                        else RunCursor.REQUEST_MODEL,
                    }
                )
            }
        )
        state = RunState.model_validate(state.model_dump(mode="python"))
        await self.journal.record_activity(
            state, [self.trace.run_event(TerminationDecided(decision=decision))]
        )
        self.state = state

    async def interrupt_operation(self, *, unconfirmed: bool) -> None:
        operation = self.state.execution.active_operation
        if operation is None:
            return
        if operation.kind not in ("model", "summary"):
            raise ValueError("Model interruption requires a model or summary attempt")
        trace = deepcopy(self.trace)
        event = trace.run_event(
            ModelResponded(
                attempt_id=operation.attempt_id,
                finish_reason="interrupted_unconfirmed"
                if unconfirmed
                else "interrupted",
                tool_call_count=0,
            )
        )
        usage = self.state.usage.model_copy(
            update={
                "unconfirmed_model_requests": self.state.usage.unconfirmed_model_requests
                + int(unconfirmed)
            }
        )
        state = self.state.model_copy(
            update={
                "usage": usage,
                "execution": self.state.execution.model_copy(
                    update={"active_operation": None}
                ),
            }
        )
        if operation.kind == "summary":
            state = state.model_copy(
                update={
                    "execution": state.execution.model_copy(
                        update={
                            "request_progress": state.execution.request_progress.model_copy(
                                update={"retry_pending": True}
                            )
                        }
                    )
                }
            )
        if trace.has_model:
            trace.end_model()
        await self.journal.record_activity(state, [event])
        self.state, self.trace = state, trace

    async def _start(self) -> None:
        await self.journal.create(self.state)
        next_state = start_running(prepare(self.state))
        await self.journal.start_run(
            next_state,
            [self.trace.run_event(RunStarted())],
        )
        self.state = next_state

    def before_next_step(self) -> RunTermination | None:
        if self.state.execution.steps_started < self.state.definition.config.max_steps:
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.MAX_STEPS_REACHED,
        )

    async def begin_compaction_attempt(
        self,
        plan: CompactionPlan,
        attempt: int,
        *,
        branch: Literal["boundary", "forced_compaction"] = "boundary",
    ) -> None:
        progress = self.state.execution.request_progress
        used = progress.summary_retry_used + int(progress.retry_pending)
        if used > self.state.definition.config.compaction_max_retries:
            raise ValueError("Summary retry balance exhausted")
        progress = progress.model_copy(
            update={
                "branch": branch,
                "summary_retry_used": used,
                "retry_pending": False,
                "source_version": progress.source_version
                or plan.source_snapshot_version,
                "last_failure": None,
            }
        )
        state = self.state.model_copy(
            update={
                "usage": self.state.usage.model_copy(
                    update={"model_requests": self.state.usage.model_requests + 1}
                ),
                "execution": self.state.execution.model_copy(
                    update={"request_progress": progress}
                ),
            }
        )
        target = state.definition.agent_spec.model
        event = self.trace.run_event(
            CompactionRequested(
                hard_trigger=plan.hard_trigger,
                provider=target.provider,
                model=target.model,
                attempt=attempt,
                source_snapshot_version=plan.source_snapshot_version,
                covered_messages=plan.covered_messages,
            )
        )
        record = self.journal.recorder.record
        state = state.model_copy(
            update={
                "execution": state.execution.model_copy(
                    update={
                        "active_operation": OperationRef(
                            kind="summary",
                            attempt_id=uuid4(),
                            started_event_id=event.event_id,
                            started_sequence=record.last_event_sequence + 1
                            if record
                            else 1,
                            step_number=state.execution.current_step.step_number
                            if state.execution.current_step
                            else None,
                        )
                    }
                )
            }
        )
        await self.journal.record_activity(state, [event])
        self.state = state

    async def record_compaction_attempt(
        self, attempt: CompactionAttempt, *, failure: RunError | None = None
    ) -> None:
        state = self.state
        state = state.model_copy(
            update={
                "execution": state.execution.model_copy(
                    update={
                        "active_operation": None,
                        "request_progress": state.execution.request_progress.model_copy(
                            update={"retry_pending": True, "last_failure": failure}
                        ),
                    }
                )
            }
        )
        if attempt.usage is None:
            state = state.model_copy(
                update={
                    "usage": state.usage.model_copy(
                        update={
                            "unconfirmed_model_requests": state.usage.unconfirmed_model_requests
                            + 1
                        }
                    )
                }
            )
        if attempt.usage is not None:
            state = state.model_copy(
                update={
                    "usage": add_request_usage(
                        state.usage, attempt.usage, attempt.request_cost
                    )
                }
            )
        await self.journal.record_activity(
            state, [self.trace.run_event(CompactionAttemptRecorded(result=attempt))]
        )
        self.state = state

    async def fail_compaction(
        self, code: str, reason: str, attempts: tuple[CompactionAttempt, ...] = ()
    ) -> None:
        await self.journal.record_activity(
            self.state,
            [
                self.trace.run_event(
                    CompactionFailed(code=code, reason=reason, attempts=attempts)
                )
            ],
        )

    async def publish_compaction(
        self,
        conversation: ConversationState,
        plan: CompactionPlan,
        *,
        attempts: tuple[CompactionAttempt, ...],
        new_tokens: int,
        summary_input_tokens: int,
        source_snapshot_id: UUID | None,
    ) -> None:
        state, event = self.journal.prepare_compaction(
            self.state,
            conversation,
            plan,
            attempts=attempts,
            new_tokens=new_tokens,
            summary_input_tokens=summary_input_tokens,
            source_snapshot_id=source_snapshot_id,
        )
        await self.journal.compact(state, event)
        self.state = state

    async def begin_step(self, *, provider: str, model: str) -> None:
        next_state = advance_step(self.state)
        next_state = next_state.model_copy(
            update={
                "execution": next_state.execution.model_copy(
                    update={"request_progress": RequestProgress(branch="business")}
                )
            }
        )
        next_trace = deepcopy(self.trace)
        next_trace.begin_step(next_state.execution.steps_started)
        step_started = next_trace.step_event(StepStarted())
        record = self.journal.recorder.record
        next_state = RunState.model_validate(
            next_state.model_copy(
                update={
                    "execution": next_state.execution.model_copy(
                        update={
                            "current_step": CurrentStep(
                                step_number=next_state.execution.steps_started,
                                message_start=len(self.state.conversation.messages),
                                started_event_id=step_started.event_id,
                                started_sequence=record.last_event_sequence + 1
                                if record
                                else 1,
                            ),
                        }
                    ),
                }
            ).model_dump(mode="python")
        )
        next_trace.begin_model()
        attempt_id = uuid4()
        model_requested = next_trace.model_event(
            ModelRequested(provider=provider, model=model, attempt_id=attempt_id)
        )
        next_state = next_state.model_copy(
            update={
                "execution": next_state.execution.model_copy(
                    update={
                        "active_operation": OperationRef(
                            kind="model",
                            attempt_id=attempt_id,
                            started_event_id=model_requested.event_id,
                            started_sequence=record.last_event_sequence + 2
                            if record
                            else 2,
                            step_number=next_state.execution.steps_started,
                        )
                    }
                )
            }
        )
        await self.journal.record_activity(
            next_state,
            [step_started, model_requested],
        )
        self.state = next_state
        self.trace = next_trace

    async def record_model_failure(self, code: str) -> None:
        operation = self.state.execution.active_operation
        trace = deepcopy(self.trace)
        event = trace.model_event(
            ModelResponded(
                attempt_id=operation.attempt_id if operation else None,
                finish_reason="error",
                tool_call_count=0,
                error_code=code,
            )
        )
        trace.end_model()
        state = self.state.model_copy(
            update={
                "execution": self.state.execution.model_copy(
                    update={"active_operation": None}
                ),
                "usage": self.state.usage.model_copy(
                    update={
                        "unconfirmed_model_requests": self.state.usage.unconfirmed_model_requests
                        + 1
                    }
                ),
            }
        )
        if (
            code == "CONTEXT_CAPACITY_EXCEEDED"
            and state.execution.request_progress.business_retry_used == 0
        ):
            state = state.model_copy(
                update={
                    "execution": state.execution.model_copy(
                        update={
                            "request_progress": RequestProgress(
                                branch="forced_compaction"
                            )
                        }
                    )
                }
            )
        await self.journal.record_activity(state, [event])
        self.state, self.trace = state, trace

    async def begin_model_retry(self, *, context_retry: bool = False) -> None:
        trace = deepcopy(self.trace)
        trace.begin_model()
        target = self.state.definition.agent_spec.model
        state = self.state.model_copy(
            update={
                "usage": self.state.usage.model_copy(
                    update={"model_requests": self.state.usage.model_requests + 1}
                )
            }
        )
        attempt_id = uuid4()
        event = trace.model_event(
            ModelRequested(
                provider=target.provider,
                model=target.model,
                attempt=state.usage.model_requests,
                attempt_id=attempt_id,
            )
        )
        record = self.journal.recorder.record
        state = state.model_copy(
            update={
                "execution": state.execution.model_copy(
                    update={
                        "active_operation": OperationRef(
                            kind="model",
                            attempt_id=attempt_id,
                            started_event_id=event.event_id,
                            started_sequence=record.last_event_sequence + 1
                            if record
                            else 1,
                            step_number=state.execution.steps_started,
                        )
                    }
                )
            }
        )
        if context_retry:
            progress = state.execution.request_progress
            if progress.business_retry_used >= 1:
                raise ValueError("Business context retry balance exhausted")
            state = state.model_copy(
                update={
                    "execution": state.execution.model_copy(
                        update={
                            "request_progress": progress.model_copy(
                                update={
                                    "branch": "business",
                                    "business_retry_used": 1,
                                    "retry_pending": False,
                                }
                            )
                        }
                    )
                }
            )
        await self.journal.record_activity(state, [event])
        self.state = state
        self.trace = trace

    async def record_model_response(
        self,
        response: ModelResponse,
        request_cost: RequestCost | None,
        *,
        disposition: ResponseDisposition,
    ) -> None:
        self.account_active_time()
        operation = self.state.execution.active_operation
        next_state = accept_model_response(
            self.state,
            response,
            request_cost,
            disposition=disposition,
        )
        if response.usage is None:
            next_state = next_state.model_copy(
                update={
                    "usage": next_state.usage.model_copy(
                        update={
                            "unconfirmed_model_requests": next_state.usage.unconfirmed_model_requests
                            + 1
                        }
                    )
                }
            )
        next_trace = deepcopy(self.trace)
        event = next_trace.model_event(
            ModelResponded(
                attempt_id=operation.attempt_id if operation else None,
                finish_reason=response.finish_reason.value,
                tool_call_count=len(response.message.tool_calls),
            )
        )
        next_trace.end_model()
        await self.journal.record_activity(
            next_state,
            [event],
        )
        self.state = next_state
        self.trace = next_trace

    async def record_policy_decision(self, decision: PolicyDecision) -> None:
        next_state = self.state
        next_trace = deepcopy(self.trace)
        request = decision.request
        await self.journal.record_activity(
            next_state,
            [
                next_trace.tool_event(
                    PolicyDecided(
                        call_index=request.call_index,
                        tool_call_id=request.tool_call_id,
                        tool_name=request.tool_name,
                        decision=decision,
                    )
                )
            ],
        )
        self.state = next_state
        self.trace = next_trace

    async def complete_tool_step(
        self,
        results: list[ToolResult],
    ) -> RunTermination | None:
        failed = tool_step_failed(results)
        next_state = advance_tool_step(self.state, failed=failed)
        next_trace = deepcopy(self.trace)
        event = next_trace.step_event(
            StepCompleted(
                failed=failed,
                consecutive_failed_steps=(
                    next_state.execution.consecutive_failed_steps
                ),
            )
        )
        next_trace.end_step()
        await self.journal.complete_step(
            next_state,
            [event],
        )
        self.state = next_state
        self.trace = next_trace
        if (
            next_state.execution.consecutive_failed_steps
            < next_state.definition.config.max_consecutive_failures
        ):
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
        )

    async def begin_evaluation(self, candidate_output: str) -> None:
        next_state = transition_to_evaluation(self.state, candidate_output)
        next_trace = deepcopy(self.trace)
        event = next_trace.step_event(
            StepCompleted(failed=False, candidate_output=True)
        )
        next_trace.end_step()
        next_trace.begin_evaluation()
        evaluation_start = next_trace.evaluation_event(EvaluationStart())
        await self.journal.complete_step(
            next_state,
            [event, evaluation_start],
        )
        self.state = next_state
        self.trace = next_trace

    def stage_evaluation_result(
        self,
        *,
        passed: bool,
        final_output: str | None = None,
        artifacts: list[ArtifactRef] | None = None,
    ) -> None:
        self.pending_evaluation_result = _PendingEvaluationResult(
            passed=passed,
            final_output=final_output,
            artifacts=tuple(artifacts or ()),
        )

    async def begin_tool(
        self,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        next_trace = deepcopy(self.trace)
        correlation = ToolCorrelation(
            call_index=call_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        )
        next_trace.begin_tool(correlation)
        await self.journal.record_activity(
            self.state,
            [
                next_trace.tool_event(
                    ToolStarted(
                        call_index=call_index,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    )
                )
            ],
        )
        self.trace = next_trace

    async def begin_tool_execution(self) -> None:
        next_state = count_tool_execution(self.state)
        next_trace = deepcopy(self.trace)
        next_trace.begin_tool_execution()
        correlation = next_trace.tool_correlation
        attempt_id = uuid4()
        event = next_trace.tool_execution_event(
            ToolExecutionStarted(
                attempt_id=attempt_id,
                call_index=correlation.call_index,
                tool_call_id=correlation.tool_call_id,
                tool_name=correlation.tool_name,
            )
        )
        record = self.journal.recorder.record
        operation = OperationRef(
            kind="tool",
            attempt_id=attempt_id,
            started_event_id=event.event_id,
            started_sequence=record.last_event_sequence + 1 if record else 1,
            step_number=next_state.execution.steps_started,
            call_index=correlation.call_index,
            tool_call_id=correlation.tool_call_id,
        )
        next_state = RunState.model_validate(
            next_state.model_copy(
                update={
                    "execution": next_state.execution.model_copy(
                        update={"active_operation": operation}
                    ),
                }
            ).model_dump(mode="python")
        )
        await self.journal.record_activity(next_state, [event])
        self.state = next_state
        self.trace = next_trace

    def execution_observer(self, attempt_id: UUID) -> ExecutionObserver:
        operation = self.state.execution.active_operation
        if operation is None or operation.attempt_id != attempt_id:
            raise ManagedExecutionError(
                "Execution observer requires current operation attempt"
            )
        return _OperationObserver(self, attempt_id)

    def _tool_attempt_id(self) -> UUID:
        operation = self.state.execution.active_operation
        if operation is None or operation.kind != "tool":
            raise ManagedExecutionError("Tool completion requires active attempt")
        return operation.attempt_id

    async def complete_tool(self, result: ToolResult) -> None:
        message = ToolMessage(
            tool_call_id=result.tool_call_id,
            content=render_tool_result(
                result,
                self.state.definition.config.tool_output_limits,
            ),
        )
        next_state = append_tool_result(
            self.state,
            message=message,
            tool_call_id=result.tool_call_id,
        )
        next_trace = deepcopy(self.trace)
        correlation = next_trace.tool_correlation
        events = []
        if next_trace.has_tool_execution:
            events.append(
                next_trace.tool_execution_event(
                    ToolExecutionEnded(
                        attempt_id=self._tool_attempt_id(),
                        call_index=correlation.call_index,
                        tool_call_id=correlation.tool_call_id,
                        tool_name=correlation.tool_name,
                        status=result.status.value,
                        error_code=(
                            result.error.code if result.error is not None else None
                        ),
                    )
                )
            )
            next_trace.end_tool_execution()
        events.append(
            next_trace.tool_event(
                ToolEnded(
                    call_index=correlation.call_index,
                    tool_call_id=correlation.tool_call_id,
                    tool_name=correlation.tool_name,
                    status=result.status.value,
                    error_code=(
                        result.error.code if result.error is not None else None
                    ),
                    result=result,
                )
            )
        )
        next_trace.end_tool()
        await self.journal.record_activity(
            next_state,
            events,
        )
        self.state = next_state
        self.trace = next_trace

    async def open_approval(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> None:
        next_state = wait_for_approval(self.state, request, decision)
        next_trace = deepcopy(self.trace)
        next_trace.begin_approval()
        policy_request = decision.request
        await self.journal.open_approval(
            next_state,
            [
                next_trace.approval_event(
                    ApprovalRequested(
                        call_index=policy_request.call_index,
                        tool_call_id=policy_request.tool_call_id,
                        tool_name=policy_request.tool_name,
                        approval_id=request.approval_id,
                        decision_id=decision.decision_id,
                    )
                )
            ],
            request=request,
            decision=decision,
        )
        self.state = next_state
        self.trace = next_trace

    async def resolve_approval(
        self,
        resolution: ApprovalResolution,
    ) -> None:
        next_state = transition_from_approval(self.state, resolution)
        next_trace = deepcopy(self.trace)
        correlation = next_trace.tool_correlation
        event = next_trace.approval_event(
            ApprovalResolved(
                call_index=correlation.call_index,
                tool_call_id=correlation.tool_call_id,
                tool_name=correlation.tool_name,
                approval_id=resolution.approval_id,
                outcome=resolution.outcome.value,
            )
        )
        next_trace.end_approval()
        await self.journal.resolve_approval(
            next_state,
            [event],
            resolution,
        )
        self.state = next_state
        self.trace = next_trace

    async def invalidate_approval(
        self,
        approval_id: UUID,
        *,
        reason: str,
    ) -> None:
        await self.journal.invalidate_approval(
            self.state,
            [
                self.trace.tool_event(
                    ApprovalInvalidated(
                        approval_id=approval_id,
                        reason=reason,
                    )
                )
            ],
            approval_id,
        )

    def stage_approval_resolution(
        self,
        resolution: ApprovalResolution,
    ) -> None:
        self.pending_approval_resolution = _PendingApprovalResolution(
            resolution=resolution,
        )

    async def finish(
        self,
        termination: RunTermination,
    ) -> RunResult:
        self.account_active_time()
        pending = self.pending_approval_resolution
        if (
            termination.status is RunStatus.CANCELLED
            and self.state.approval.pending is not None
            and pending is None
        ):
            resolution = ApprovalResolution(
                approval_id=self.state.approval.pending.approval_id,
                outcome=ApprovalOutcome.INVALIDATED,
                actor_kind="system",
                actor_id="runtime",
                reason="The run was cancelled.",
                resolved_at=datetime.now(UTC),
            )
            pending = _PendingApprovalResolution(resolution=resolution)

        evaluation = self.pending_evaluation_result
        next_state = finish_state(
            self.state,
            termination.status,
            termination.reason,
            error=termination.error,
            approval_resolution=(pending.resolution if pending is not None else None),
            final_output=(evaluation.final_output if evaluation is not None else None),
        )
        next_trace = deepcopy(self.trace)
        events = []
        if pending is not None:
            correlation = next_trace.tool_correlation
            events.append(
                next_trace.approval_event(
                    ApprovalResolved(
                        call_index=correlation.call_index,
                        tool_call_id=correlation.tool_call_id,
                        tool_name=correlation.tool_name,
                        approval_id=pending.resolution.approval_id,
                        outcome=pending.resolution.outcome.value,
                    )
                )
            )
            next_trace.end_approval()
        if evaluation is not None:
            events.append(
                next_trace.evaluation_event(
                    EvaluationCompleted(passed=evaluation.passed),
                    artifacts=list(evaluation.artifacts),
                )
            )
        if next_trace.has_evaluation:
            next_trace.end_evaluation()
        if next_trace.has_model:
            next_trace.end_model()
        if next_trace.has_tool:
            correlation = next_trace.tool_correlation
            if next_trace.has_tool_execution:
                events.append(
                    next_trace.tool_execution_event(
                        ToolExecutionEnded(
                            attempt_id=self._tool_attempt_id(),
                            call_index=correlation.call_index,
                            tool_call_id=correlation.tool_call_id,
                            tool_name=correlation.tool_name,
                            status=next_state.lifecycle.status.value,
                            reason=termination.reason.value,
                        )
                    )
                )
                next_trace.end_tool_execution()
            events.append(
                next_trace.tool_event(
                    ToolEnded(
                        call_index=correlation.call_index,
                        tool_call_id=correlation.tool_call_id,
                        tool_name=correlation.tool_name,
                        status=next_state.lifecycle.status.value,
                        reason=termination.reason.value,
                    )
                )
            )
            next_trace.end_tool()
        if next_trace.has_step:
            events.append(
                next_trace.step_event(
                    StepCompleted(
                        failed=(next_state.lifecycle.status is not RunStatus.SUCCEEDED)
                    )
                )
            )
            next_trace.end_step()
        events.append(
            next_trace.run_event(
                RunFinished(
                    status=next_state.lifecycle.status.value,
                    reason=termination.reason.value,
                )
            )
        )
        await self.journal.finish_run(
            next_state,
            events,
            approval_resolution=(pending.resolution if pending is not None else None),
        )
        self.state = next_state
        self.trace = next_trace
        self.pending_approval_resolution = None
        self.pending_evaluation_result = None
        return RunResult.from_state(next_state)


@dataclass(frozen=True, slots=True)
class _OperationObserver:
    execution: RunExecution
    attempt_id: UUID

    def _operation(self) -> OperationRef:
        operation = self.execution.state.execution.active_operation
        if operation is None or operation.attempt_id != self.attempt_id:
            raise ManagedExecutionError("Observer operation is no longer current")
        return operation

    async def identified(
        self,
        identity: ExecutionIdentity,
        *,
        permission: Literal["observe", "create_exec", "start_exec"] = "observe",
    ) -> None:
        operation = self._operation()
        if identity.attempt_id != self.attempt_id:
            raise ManagedExecutionError("Execution identity belongs to another attempt")
        identities = tuple(
            item
            for item in operation.executions
            if item.execution_id != identity.execution_id
        ) + (identity,)
        state = self.execution.state
        state = RunState.model_validate(
            state.model_copy(
                update={
                    "execution": state.execution.model_copy(
                        update={
                            "active_operation": operation.model_copy(
                                update={"executions": identities}
                            )
                        }
                    ),
                }
            ).model_dump(mode="python")
        )
        await self.execution.journal.record_activity(
            state,
            [
                self.execution.trace.run_event(
                    OperationExecutionIdentified(
                        attempt_id=self.attempt_id,
                        identity=identity,
                        permission=permission,
                    )
                )
            ],
        )
        self.execution.state = state

    async def ended(self, evidence: ExecutionEvidence) -> None:
        operation = self._operation()
        if (
            evidence.execution_id != evidence.identity.execution_id
            or evidence.identity not in operation.executions
        ):
            raise ManagedExecutionError(
                "Execution evidence must match an identified execution"
            )
        await self.execution.journal.record_activity(
            self.execution.state,
            [
                self.execution.trace.run_event(
                    OperationExecutionEnded(
                        attempt_id=self.attempt_id, evidence=evidence
                    )
                )
            ],
        )
        if (
            evidence.outcome != "ended"
            or not evidence.group_ended
            or not evidence.issuer_ended
        ):
            raise ManagedExecutionError("Execution chain end is not confirmed")
