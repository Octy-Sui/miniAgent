import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import JsonValue

from miniagent.artifacts.contracts import ArtifactRef, ArtifactStore, ArtifactWrite
from miniagent.environments.evidence import CommandEvidence, CommandOutputEvidence
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    ExecutionIdentity,
    ExecutionObserver,
    ManagedExecutionError,
    current_cleanup_deadline,
)
from miniagent.evaluation import CandidateOutput, EvaluationResult
from miniagent.metering.usage import RequestCost, add_request_usage
from miniagent.models.messages import ToolMessage
from miniagent.models.protocol import (
    ModelRequest,
    ModelResponse,
    ProviderResponseEvidence,
)
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
    EvaluationResultRecorded,
    EvaluationStart,
    EventPayload,
    ModelRequested,
    ModelResponded,
    OperationExecutionEnded,
    OperationExecutionIdentified,
    OperationInterrupted,
    OperationOutputRecorded,
    PolicyDecided,
    RunEventType,
    RunFinished,
    RunStarted,
    RunSuspended,
    StepCompleted,
    StepStarted,
    TerminationDecided,
    ToolEnded,
    ToolExecutionEnded,
    ToolExecutionStarted,
    ToolStarted,
)
from miniagent.runtime.history import iter_run_events
from miniagent.runtime.interruption import (
    InterruptionRequested,
    current_interruption_token,
    raise_if_interrupted,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    ComponentEnvelope,
    CurrentStep,
    EvaluationProgress,
    ExecutionPhase,
    InterruptionInfo,
    OperationRef,
    RequestProgress,
    ResponseDisposition,
    TerminateDisposition,
    TerminationDecision,
)
from miniagent.runtime.result import (
    RunError,
    RunResult,
    RunSuspension,
    TerminationReason,
)
from miniagent.runtime.state import ConversationState, RunCursor, RunState, RunStatus
from miniagent.runtime.store_contracts import RunRecord
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
from miniagent.runtime.transitions import (
    suspend as suspend_state,
)
from miniagent.tools.contracts import (
    ArtifactToolResultEvidence,
    InlineToolResultEvidence,
    ToolResult,
    ToolResultEvidence,
    ToolReturnValidation,
)
from miniagent.tools.rendering import render_tool_result


class EvaluationObserver(Protocol):
    @property
    def progress(self) -> EvaluationProgress: ...

    async def record(
        self, payload: EventPayload, *, component: ComponentEnvelope | None = None
    ) -> None: ...

    def execution_observer(self, attempt_id: UUID) -> ExecutionObserver: ...


_EVALUATION_OBSERVER: ContextVar[EvaluationObserver | None] = ContextVar(
    "miniagent_evaluation_observer", default=None
)


@contextmanager
def evaluation_scope(observer: EvaluationObserver) -> Iterator[None]:
    token = _EVALUATION_OBSERVER.set(observer)
    try:
        yield
    finally:
        _EVALUATION_OBSERVER.reset(token)


def current_evaluation_observer() -> EvaluationObserver | None:
    return _EVALUATION_OBSERVER.get()


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
class RunExecution:
    state: RunState
    journal: RunJournal
    trace: RunTraceContext
    artifact_store: ArtifactStore | None = None
    _active_mark: float = field(default_factory=monotonic)
    _evaluation_attempt_id: UUID | None = None
    _tool_results: dict[int, ToolResult] = field(default_factory=dict)
    _ended_executions: dict[UUID, ExecutionEvidence] = field(default_factory=dict)
    pending_approval_resolution: _PendingApprovalResolution | None = None

    @classmethod
    async def start(
        cls,
        state: RunState,
        journal: RunJournal,
        *,
        on_record_created: Callable[[RunRecord], None] | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> "RunExecution":
        execution = cls(
            state=state,
            journal=journal,
            trace=RunTraceContext.create(state.identity.run_id),
            artifact_store=artifact_store,
        )
        await execution._start(on_record_created=on_record_created)
        return execution

    @classmethod
    def attach(
        cls,
        record: RunRecord,
        journal: RunJournal,
        *,
        artifact_store: ArtifactStore | None = None,
    ) -> "RunExecution":
        if journal.recorder.record != record:
            raise ValueError("Attached journal differs from current Run record")
        state = RunState.model_validate(record.state.model_dump(mode="python"))
        trace = RunTraceContext.create(state.identity.run_id)
        if state.execution.current_step is not None:
            trace.begin_step(state.execution.current_step.step_number)
            operation = state.execution.active_operation
            if operation is not None and operation.kind == "model":
                trace.begin_model()
            if operation is not None and operation.kind == "tool":
                batch = state.execution.tool_batch
                assert batch is not None and operation.call_index is not None
                call = batch.calls[operation.call_index]
                trace.begin_tool(
                    ToolCorrelation(
                        call_index=operation.call_index,
                        tool_call_id=call.id,
                        tool_name=call.name,
                    )
                )
                trace.begin_tool_execution()
        if state.lifecycle.status is RunStatus.EVALUATING:
            trace.begin_evaluation()
        return cls(
            state=state, journal=journal, trace=trace, artifact_store=artifact_store
        )

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

    @staticmethod
    def _with_termination(state: RunState, decision: TerminationDecision) -> RunState:
        state = state.model_copy(
            update={
                "execution": state.execution.model_copy(
                    update={
                        "phase": ExecutionPhase.TERMINATE,
                        "response_disposition": TerminateDisposition(decision=decision),
                        "active_operation": None,
                        "tool_batch": None,
                        "cursor": state.execution.cursor
                        if state.lifecycle.status is not RunStatus.RUNNING
                        else RunCursor.REQUEST_MODEL,
                    }
                )
            }
        )
        state = RunState.model_validate(state.model_dump(mode="python"))
        return state

    async def record_termination(self, decision: TerminationDecision) -> None:
        if (
            self.state.execution.phase is ExecutionPhase.TERMINATE
            and self.state.execution.response_disposition
            == TerminateDisposition(decision=decision)
        ):
            return
        self.account_active_time()
        trace = deepcopy(self.trace)
        state = self.state
        events = []
        resolution = None
        if state.approval.pending is not None:
            resolution = (
                self.pending_approval_resolution.resolution
                if self.pending_approval_resolution
                else ApprovalResolution(
                    approval_id=state.approval.pending.approval_id,
                    outcome=ApprovalOutcome.INVALIDATED,
                    actor_kind="system",
                    actor_id="runtime",
                    reason="The run stopped before authorization.",
                    resolved_at=datetime.now(UTC),
                )
            )
            correlation = trace.tool_correlation
            events.append(
                trace.approval_event(
                    ApprovalResolved(
                        call_index=correlation.call_index,
                        tool_call_id=correlation.tool_call_id,
                        tool_name=correlation.tool_name,
                        approval_id=resolution.approval_id,
                        outcome=resolution.outcome.value,
                    )
                )
            )
            trace.end_approval()
            state = transition_from_approval(state, resolution)
        state = self._with_termination(state, decision)
        events.append(trace.run_event(TerminationDecided(decision=decision)))
        if resolution is not None:
            await self.journal.resolve_approval(state, events, resolution)
        else:
            await self.journal.record_activity(state, events)
        self.state, self.trace = state, trace
        self.pending_approval_resolution = None

    async def interrupt_operation(
        self, *, unconfirmed: bool, termination: TerminationDecision | None = None
    ) -> None:
        operation = self.state.execution.active_operation
        if operation is None:
            return
        metered = operation.kind in ("model", "summary")
        if not metered:
            await self._require_operation_ended()
        trace = deepcopy(self.trace)
        event = trace.run_event(
            OperationInterrupted(
                attempt_id=operation.attempt_id,
                operation_kind=operation.kind,
                unconfirmed=unconfirmed,
            )
        )
        usage = self.state.usage.model_copy(
            update={
                "unconfirmed_model_requests": self.state.usage.unconfirmed_model_requests
                + int(unconfirmed and metered)
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
        if trace.has_tool_execution:
            trace.end_tool_execution()
        if trace.has_tool:
            trace.end_tool()
        events = [event]
        if termination is not None:
            state = self._with_termination(state, termination)
            events.append(trace.run_event(TerminationDecided(decision=termination)))
        await self.journal.record_activity(state, events)
        self.state, self.trace = state, trace

    async def _start(
        self, *, on_record_created: Callable[[RunRecord], None] | None = None
    ) -> None:
        await self.journal.create(self.state)
        if on_record_created is not None and self.journal.recorder.record is not None:
            on_record_created(self.journal.recorder.record.model_copy(deep=True))
        next_state = start_running(prepare(self.state))
        await self.journal.start_run(
            next_state,
            [self.trace.run_event(RunStarted())],
        )
        self.state = next_state

    def before_next_step(self) -> RunTermination | None:
        if (
            self.state.execution.consecutive_failed_steps
            >= self.state.definition.config.max_consecutive_failures
        ):
            return RunTermination(
                status=RunStatus.FAILED,
                reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
            )
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
        request: ModelRequest,
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
        attempt_id = uuid4()
        event = self.trace.run_event(
            CompactionRequested(
                request=request,
                attempt_id=attempt_id,
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
                            attempt_id=attempt_id,
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
        self,
        attempt: CompactionAttempt,
        *,
        failure: RunError | None = None,
        response: ModelResponse | None = None,
        normalization_failure: ProviderResponseEvidence | None = None,
    ) -> None:
        operation = self.state.execution.active_operation
        if operation is None or operation.kind != "summary":
            raise ValueError("Compaction attempt requires an active summary operation")
        attempt = attempt.model_copy(update={"attempt_id": operation.attempt_id})
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
            state,
            [
                self.trace.run_event(
                    CompactionAttemptRecorded(
                        result=attempt,
                        attempt_id=operation.attempt_id,
                        response=response,
                        normalization_failure=normalization_failure,
                    )
                )
            ],
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

    async def begin_step(
        self, *, provider: str, model: str, request: ModelRequest
    ) -> None:
        next_state = advance_step(
            start_running(self.state)
            if self.state.lifecycle.status is RunStatus.PREPARING
            else self.state
        )
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
            ModelRequested(
                provider=provider, model=model, attempt_id=attempt_id, request=request
            )
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

    async def record_model_failure(
        self, code: str, *, message: str | None = None
    ) -> None:
        operation = self.state.execution.active_operation
        trace = deepcopy(self.trace)
        event = trace.model_event(
            ModelResponded(
                attempt_id=operation.attempt_id if operation else None,
                finish_reason="error",
                tool_call_count=0,
                error_code=code,
                cost_status="usage_missing",
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
        events = [event]
        if (
            code == "CONTEXT_CAPACITY_EXCEEDED"
            and state.execution.request_progress.business_retry_used >= 1
        ):
            decision = TerminationDecision(
                status="failed",
                reason=TerminationReason.MODEL_FAILED,
                error=RunError(
                    code=code,
                    message=message
                    or "Provider rejected context after the original retry",
                ),
            )
            state = self._with_termination(state, decision)
            events.append(trace.run_event(TerminationDecided(decision=decision)))
        await self.journal.record_activity(state, events)
        self.state, self.trace = state, trace

    async def begin_model_retry(
        self, *, request: ModelRequest, context_retry: bool = False
    ) -> None:
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
                request=request,
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
        response: ModelResponse | None,
        request_cost: RequestCost | None,
        *,
        disposition: ResponseDisposition,
        normalization_failure: ProviderResponseEvidence | None = None,
        cost_status: Literal[
            "calculated",
            "not_configured",
            "usage_missing",
            "invalid_usage",
            "calculation_failed",
        ]
        | None = None,
    ) -> None:
        self.account_active_time()
        operation = self.state.execution.active_operation
        next_state = accept_model_response(
            self.state,
            response,
            request_cost,
            disposition=disposition,
        )
        if response is None or response.usage is None:
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
                finish_reason=response.finish_reason.value if response else "error",
                tool_call_count=len(response.message.tool_calls) if response else 0,
                response=response,
                normalization_failure=normalization_failure,
                request_cost=request_cost,
                cost_status=cost_status
                or (
                    "calculated"
                    if request_cost is not None
                    else "invalid_usage"
                    if response is None
                    else "usage_missing"
                    if response.usage is None
                    else "not_configured"
                ),
                disposition=disposition,
                error=disposition.decision.error
                if isinstance(disposition, TerminateDisposition)
                else None,
                error_code=disposition.decision.error.code
                if isinstance(disposition, TerminateDisposition)
                and disposition.decision.error
                else None,
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

    async def complete_tool_step(self) -> RunTermination | None:
        batch = self.state.execution.tool_batch
        if batch is None or batch.next_index != len(batch.calls):
            raise ValueError("Tool step cannot finish before every call completes")
        store = self.journal.recorder.store
        if store is not None:
            facts = await store.list_tool_results(
                self.state.identity.run_id,
                step_number=self.state.execution.steps_started,
            )
            results = [fact.result for fact in facts]
        else:
            if sorted(self._tool_results) != list(range(len(batch.calls))):
                raise ValueError(
                    "In-process tool results do not cover the complete batch"
                )
            results = [self._tool_results[index] for index in range(len(batch.calls))]
        if len(results) != len(batch.calls) or any(
            result.tool_call_id != call.id
            for result, call in zip(results, batch.calls, strict=True)
        ):
            raise ValueError("Tool results do not match the complete batch")
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
        self._tool_results.clear()
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
        if self.state.execution.active_operation is not None:
            raise ManagedExecutionError(
                "Prior evaluation operation must be interrupted before restarting"
            )
        next_state = self.state
        next_trace = deepcopy(self.trace)
        events = []
        if self.state.execution.current_step is not None:
            next_state = transition_to_evaluation(self.state, candidate_output)
            events.append(
                next_trace.step_event(
                    StepCompleted(failed=False, candidate_output=True)
                )
            )
            next_trace.end_step()
        if not next_trace.has_evaluation:
            next_trace.begin_evaluation()
        attempt_id = uuid4()
        event = next_trace.evaluation_event(EvaluationStart(attempt_id=attempt_id))
        events.append(event)
        record = self.journal.recorder.record
        operation = OperationRef(
            kind="evaluation",
            attempt_id=attempt_id,
            started_event_id=event.event_id,
            started_sequence=(record.last_event_sequence if record else 0)
            + len(events),
        )
        next_state = next_state.model_copy(
            update={
                "execution": next_state.execution.model_copy(
                    update={"active_operation": operation}
                )
            }
        )
        if len(events) > 1:
            await self.journal.complete_step(next_state, events)
        else:
            await self.journal.record_activity(next_state, events)
        self.state, self.trace = next_state, next_trace
        self._evaluation_attempt_id = attempt_id

    def evaluation_observer(self) -> EvaluationObserver:
        return _EvaluationObserver(self)

    async def _require_operation_ended(self) -> None:
        operation = self.state.execution.active_operation
        if operation is None or not operation.executions:
            return
        store = self.journal.recorder.store
        evidence = dict(self._ended_executions)
        if store is not None:
            evidence = {}
            record = self.journal.recorder.record
            assert record is not None
            async for event in iter_run_events(
                store,
                self.state.identity.run_id,
                after_sequence=operation.started_sequence,
                through_sequence=record.last_event_sequence,
                types=(
                    RunEventType.OPERATION_EXECUTION_IDENTIFIED,
                    RunEventType.OPERATION_EXECUTION_ENDED,
                ),
            ):
                payload = event.payload
                if (
                    isinstance(payload, OperationExecutionIdentified)
                    and payload.attempt_id == operation.attempt_id
                ):
                    evidence.pop(payload.identity.execution_id, None)
                elif (
                    isinstance(payload, OperationExecutionEnded)
                    and payload.attempt_id == operation.attempt_id
                ):
                    evidence[payload.evidence.execution_id] = payload.evidence
        for identity in operation.executions:
            item = evidence.get(identity.execution_id)
            if (
                item is None
                or item.identity != identity
                or item.outcome != "ended"
                or not item.group_ended
                or not item.issuer_ended
            ):
                raise ManagedExecutionError(
                    "Operation execution chain end is unconfirmed"
                )

    async def record_component_fact(
        self, payload: EventPayload, *, component: ComponentEnvelope | None = None
    ) -> None:
        await self._require_operation_ended()
        kind = payload.type.value
        progress = self.state.execution.evaluation
        operation = self.state.execution.active_operation
        if kind.startswith("candidate_submission_"):
            operation_kind = "patch_export"
            if component is not None:
                if component.kind != "candidate_submission":
                    raise ValueError("Candidate component envelope kind differs")
                progress = progress.model_copy(update={"submission": component})
        elif kind.startswith("evaluation_attempt_"):
            operation_kind = "evaluation"
            if component is not None:
                if component.kind != "swebench_attempt":
                    raise ValueError("Evaluation component envelope kind differs")
                progress = progress.model_copy(update={"attempt": component})
        else:
            raise ValueError("Unsupported evaluation component fact")
        event = self.trace.evaluation_event(payload)
        if kind.endswith("_started"):
            record = self.journal.recorder.record
            attempt_id = getattr(payload, "attempt_id", None)
            if not isinstance(attempt_id, UUID):
                raise ValueError("Component started fact requires attempt_id")
            operation = OperationRef(
                kind=operation_kind,
                attempt_id=attempt_id,
                started_event_id=event.event_id,
                started_sequence=record.last_event_sequence + 1 if record else 1,
                component=component,
            )
        elif kind.endswith(("_committed", "_failed", "_interrupted")):
            attempt_id = getattr(payload, "attempt_id", None)
            if operation is not None and operation.attempt_id == attempt_id:
                operation = None
            elif kind == "evaluation_attempt_interrupted":
                store = self.journal.recorder.store
                confirmed = False
                record = self.journal.recorder.record
                if store is not None and record is not None:
                    async for previous in iter_run_events(
                        store,
                        self.state.identity.run_id,
                        through_sequence=record.last_event_sequence,
                        types=(RunEventType.OPERATION_INTERRUPTED,),
                    ):
                        if (
                            isinstance(previous.payload, OperationInterrupted)
                            and previous.payload.attempt_id == attempt_id
                        ):
                            confirmed = True
                            break
                if not confirmed:
                    raise ManagedExecutionError(
                        "Previous evaluation attempt has no confirmed interruption"
                    )
            else:
                raise ManagedExecutionError(
                    "Component completion belongs to another active operation"
                )
        state = self.state.model_copy(
            update={
                "execution": self.state.execution.model_copy(
                    update={"evaluation": progress, "active_operation": operation}
                )
            }
        )
        await self.journal.record_activity(state, [event])
        self.state = state

    async def record_evaluation_result(self, result: EvaluationResult) -> None:
        await self._require_operation_ended()
        if self.state.execution.evaluation.result is not None:
            raise ValueError("Evaluation result is already committed")
        operation = self.state.execution.active_operation
        if operation is not None and operation.kind != "evaluation":
            raise ManagedExecutionError(
                "Evaluation result cannot close another active operation"
            )
        attempt_id = operation.attempt_id if operation else self._evaluation_attempt_id
        if attempt_id is None:
            raise ValueError("Evaluation result requires current evaluation attempt")
        output = (
            (
                result.output
                if result.output is not None
                else self.state.lifecycle.candidate_output
            )
            if result.passed
            else None
        )
        decision = TerminationDecision(
            status="succeeded" if result.passed else "failed",
            reason=TerminationReason.TASK_VERIFIED
            if result.passed
            else TerminationReason.EVALUATION_FAILED,
            error=RunError(
                code=result.error.code,
                message=result.error.message,
                retryable=result.error.retryable,
            )
            if result.error
            else None,
            final_output=output,
        )
        state = self.state.model_copy(
            update={
                "execution": self.state.execution.model_copy(
                    update={
                        "evaluation": self.state.execution.evaluation.model_copy(
                            update={"result": result.model_copy(deep=True)}
                        ),
                        "active_operation": None,
                        "phase": ExecutionPhase.TERMINATE,
                        "response_disposition": TerminateDisposition(decision=decision),
                    }
                )
            }
        )
        state = RunState.model_validate(state.model_dump(mode="python"))
        await self.journal.record_activity(
            state,
            [
                self.trace.evaluation_event(
                    EvaluationResultRecorded(attempt_id=attempt_id, result=result),
                    artifacts=result.artifacts,
                )
            ],
        )
        self.state = state

    async def begin_tool(
        self,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        batch = self.state.execution.tool_batch
        if batch is None or not 0 <= call_index < len(batch.calls):
            raise ValueError("Tool start requires a current batch call")
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
                        arguments=batch.calls[call_index].arguments,
                        call_index=call_index,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                    )
                )
            ],
        )
        self.trace = next_trace

    async def begin_tool_execution(
        self, *, canonical_arguments: dict[str, JsonValue] | None = None
    ) -> None:
        next_state = count_tool_execution(self.state)
        next_trace = deepcopy(self.trace)
        next_trace.begin_tool_execution()
        correlation = next_trace.tool_correlation
        attempt_id = uuid4()
        event = next_trace.tool_execution_event(
            ToolExecutionStarted(
                canonical_arguments=canonical_arguments,
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

    async def capture_tool_result(self, result: ToolResult) -> ToolResultEvidence:
        if self.artifact_store is None:
            raise ValueError("Tool evidence requires artifact_store")
        operation = self.state.execution.active_operation
        if operation is None or operation.kind != "tool":
            raise ValueError("Tool evidence requires an active tool attempt")
        encoded = result.model_dump_json().encode("utf-8")
        if len(encoded) <= 65536:
            return InlineToolResultEvidence(
                result=ToolResult.model_validate_json(encoded)
            )

        def deadline() -> float | None:
            token = current_interruption_token()
            deadlines = [
                value
                for value in (
                    current_cleanup_deadline(),
                    token.deadline if token else None,
                )
                if value is not None
            ]
            return min(deadlines) if deadlines else None

        writer = await self.artifact_store.open_writer(
            ArtifactWrite(
                run_id=self.state.identity.run_id,
                type="tool_result",
                media_type="application/json",
                metadata={
                    "attempt_id": str(operation.attempt_id),
                    "tool_call_id": operation.tool_call_id,
                    "step_number": operation.step_number,
                    "call_index": operation.call_index,
                },
            ),
            deadline=deadline(),
        )
        try:
            for offset in range(0, len(encoded), 65536):
                limit = deadline()
                if limit is not None and monotonic() >= limit:
                    raise TimeoutError("Tool evidence persistence deadline exceeded")
                await writer.write(encoded[offset : offset + 65536])
            ref = await writer.finish(deadline=deadline())
        except BaseException:
            await writer.abort()
            raise
        return ArtifactToolResultEvidence(ref=ref)

    async def complete_tool(
        self,
        result: ToolResult,
        *,
        raw_result: ToolResultEvidence | None = None,
        return_validation: ToolReturnValidation | None = None,
        artifacts: tuple[ArtifactRef, ...] | None = None,
    ) -> RunTermination | None:
        await self._require_operation_ended()
        operation = self.state.execution.active_operation
        result = result.model_copy(deep=True)
        if return_validation is None:
            if operation is not None and operation.kind == "tool":
                raw_result = await self.capture_tool_result(result)
                return_validation = ToolReturnValidation(disposition="accepted")
            else:
                return_validation = ToolReturnValidation(
                    disposition="not_returned", reason="tool_not_executed"
                )
        source_artifacts = list(result.artifacts if artifacts is None else artifacts)
        if isinstance(raw_result, ArtifactToolResultEvidence):
            source_artifacts.append(raw_result.ref)
        message = None
        effective = None
        termination = None
        try:
            message = ToolMessage(
                tool_call_id=result.tool_call_id,
                content=render_tool_result(
                    result, self.state.definition.config.tool_output_limits
                ),
            )
            effective = ToolResult.model_validate_json(message.content)
        except (TypeError, ValueError):
            message = None
            termination = RunTermination(
                status=RunStatus.FAILED,
                reason=TerminationReason.INTERNAL_ERROR,
                error=RunError(
                    code="TOOL_OBSERVATION_UNAVAILABLE",
                    message="Tool result could not be rendered for the model",
                ),
            )
            decision = TerminationDecision(
                status="failed", reason=termination.reason, error=termination.error
            )
            next_state = self._with_termination(self.state, decision)
        else:
            next_state = append_tool_result(
                self.state, message=message, tool_call_id=result.tool_call_id
            )
        next_trace = deepcopy(self.trace)
        correlation = next_trace.tool_correlation
        events = []
        status = effective.status.value if effective is not None else "error"
        error_code = (
            effective.error.code
            if effective is not None and effective.error
            else termination.error.code
            if termination and termination.error
            else None
        )
        if next_trace.has_tool_execution:
            events.append(
                next_trace.tool_execution_event(
                    ToolExecutionEnded(
                        attempt_id=self._tool_attempt_id(),
                        call_index=correlation.call_index,
                        tool_call_id=correlation.tool_call_id,
                        tool_name=correlation.tool_name,
                        status=status,
                        error_code=error_code,
                    )
                )
            )
            next_trace.end_tool_execution()
        events.append(
            next_trace.tool_event(
                ToolEnded(
                    attempt_id=operation.attempt_id if operation is not None else None,
                    call_index=correlation.call_index,
                    tool_call_id=correlation.tool_call_id,
                    tool_name=correlation.tool_name,
                    status=status,
                    error_code=error_code,
                    result=effective,
                    raw_result=raw_result,
                    return_validation=return_validation,
                    observation=message,
                    observation_missing_reason="render_failed"
                    if message is None
                    else None,
                )
            ).model_copy(update={"artifacts": list(dict.fromkeys(source_artifacts))})
        )
        next_trace.end_tool()
        if termination is not None:
            events.append(next_trace.run_event(TerminationDecided(decision=decision)))
        await self.journal.record_activity(next_state, events)
        if self.journal.recorder.store is None and effective is not None:
            self._tool_results[correlation.call_index] = effective.model_copy(deep=True)
        self.state = next_state
        self.trace = next_trace
        return termination

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

    async def suspend(
        self, interruption: InterruptionInfo, *, deadline: float
    ) -> RunSuspension:
        if monotonic() >= deadline:
            raise InterruptionRequested("No suspension commit budget remains")
        self.account_active_time()
        state = suspend_state(self.state, interruption)
        metadata = state.recovery.suspension
        assert metadata is not None
        events = []
        resolution = None
        pending = self.state.approval.pending
        if pending is not None:
            batch = self.state.execution.tool_batch
            assert batch is not None
            call = batch.calls[pending.call_index]
            resolution = ApprovalResolution(
                approval_id=pending.approval_id,
                outcome=ApprovalOutcome.INVALIDATED,
                actor_kind="system",
                actor_id="runtime",
                reason="The run was suspended.",
                resolved_at=datetime.now(UTC),
            )
            events.append(
                self.trace.approval_event(
                    ApprovalResolved(
                        call_index=pending.call_index,
                        tool_call_id=call.id,
                        tool_name=call.name,
                        approval_id=pending.approval_id,
                        outcome="invalidated",
                    )
                )
            )
        assert state.execution.phase is not None
        events.append(
            self.trace.run_event(
                RunSuspended(
                    interruption=interruption,
                    business_status=metadata.business_status,
                    phase=state.execution.phase,
                )
            )
        )
        async with asyncio.timeout_at(deadline):
            if self.journal.recorder.store is None:
                await self.journal.record_activity(state, events)
            else:
                await self.journal.suspend_run(
                    state, events, approval_resolution=resolution
                )
        self.state = state
        return RunSuspension(
            run_id=state.identity.run_id,
            usage=state.usage,
            started_at=state.lifecycle.started_at,
            suspended_at=metadata.suspended_at,
            interruption=interruption,
            workspace_id=self.journal.recorder.recovery_context.workspace_id
            if self.journal.recorder.recovery_context
            else None,
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

        persisted_result = self.state.execution.evaluation.result
        disposition = self.state.execution.response_disposition
        final_output = (
            disposition.decision.final_output
            if isinstance(disposition, TerminateDisposition)
            else None
        )
        next_state = finish_state(
            self.state,
            termination.status,
            termination.reason,
            error=termination.error,
            approval_resolution=(pending.resolution if pending is not None else None),
            final_output=final_output,
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
        if persisted_result is not None:
            events.append(
                next_trace.evaluation_event(
                    EvaluationCompleted(
                        passed=persisted_result.passed, result=persisted_result
                    ),
                    artifacts=persisted_result.artifacts,
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
                        raw_result=None,
                        return_validation=ToolReturnValidation(
                            disposition="not_returned", reason="run_terminated"
                        ),
                        observation=None,
                        observation_missing_reason=termination.reason.value,
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
        return RunResult.from_state(next_state)


@dataclass(frozen=True, slots=True)
class _OperationObserver:
    execution: RunExecution
    attempt_id: UUID

    def check_permission(self) -> None:
        raise_if_interrupted()

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
        command: CommandEvidence | None = None,
    ) -> None:
        raise_if_interrupted()
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
                        command=command,
                    )
                )
            ],
        )
        self.execution.state = state
        self.execution._ended_executions.pop(identity.execution_id, None)

    async def output_recorded(self, output: CommandOutputEvidence) -> None:
        operation = self._operation()
        if output.attempt_id != self.attempt_id or not any(
            identity.execution_id == output.execution_id
            for identity in operation.executions
        ):
            raise ManagedExecutionError("Output belongs to an unidentified execution")
        await self.execution.journal.record_activity(
            self.execution.state,
            [self.execution.trace.run_event(OperationOutputRecorded(output=output))],
        )

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
        self.execution._ended_executions[evidence.execution_id] = evidence


@dataclass(frozen=True, slots=True)
class _EvaluationObserver:
    execution: RunExecution

    @property
    def progress(self) -> EvaluationProgress:
        return self.execution.state.execution.evaluation

    async def record(
        self, payload: EventPayload, *, component: ComponentEnvelope | None = None
    ) -> None:
        await self.execution.record_component_fact(payload, component=component)

    def execution_observer(self, attempt_id: UUID) -> ExecutionObserver:
        return self.execution.execution_observer(attempt_id)
