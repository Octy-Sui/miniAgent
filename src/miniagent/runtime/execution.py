from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import JsonValue

from miniagent.models.model import Message, ModelResponse
from miniagent.models.tool import ToolCall, ToolResult
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.state import (
    RunCursor,
    RunError,
    RunResult,
    RunState,
    RunStatus,
    TerminationReason,
    transition,
    update_state,
)
from miniagent.runtime.termination import tool_step_failed
from miniagent.runtime.tool_output import render_tool_result
from miniagent.runtime.trace_context import RunTraceContext
from miniagent.runtime.usage import RequestCost, add_request_usage


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
    candidate_output: str


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


@dataclass(slots=True)
class RunExecution:
    state: RunState
    journal: RunJournal
    trace: RunTraceContext
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
            trace=RunTraceContext.create(state.run_id),
        )
        await execution._start()
        return execution

    async def _start(self) -> None:
        await self.journal.create(self.state)
        next_state = transition(self.state, RunStatus.PREPARING)
        next_state = transition(
            next_state,
            RunStatus.RUNNING,
            cursor=RunCursor.REQUEST_MODEL,
        )
        await self.journal.start_run(
            next_state,
            [self.trace.run_started()],
        )
        self.state = next_state

    def before_next_step(self) -> RunTermination | None:
        if self.state.steps_started < self.state.config.max_steps:
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.MAX_STEPS_REACHED,
        )

    async def begin_step(self, *, provider: str, model: str) -> None:
        next_state = update_state(
            self.state,
            steps_started=self.state.steps_started + 1,
            usage=self.state.usage.model_copy(
                update={"model_requests": self.state.usage.model_requests + 1}
            ),
        )
        next_trace = deepcopy(self.trace)
        await self.journal.record_activity(
            next_state,
            next_trace.begin_step(
                number=next_state.steps_started,
                provider=provider,
                model=model,
            ),
        )
        self.state = next_state
        self.trace = next_trace

    async def record_model_response(
        self,
        response: ModelResponse,
        request_cost: RequestCost | None,
        *,
        pending_tool_calls: Sequence[ToolCall] | None = None,
    ) -> None:
        usage = self.state.usage
        if response.usage is not None:
            usage = add_request_usage(usage, response.usage, request_cost)
        changes: dict[str, object] = {
            "usage": usage,
            "messages": [*self.state.messages, response.message],
        }
        if pending_tool_calls is not None:
            changes.update(
                pending_tool_calls=list(pending_tool_calls),
                cursor=RunCursor.EXECUTE_TOOL_CALLS,
            )
        next_state = update_state(self.state, **changes)
        next_trace = deepcopy(self.trace)
        await self.journal.record_activity(
            next_state,
            [
                next_trace.model_responded(
                    finish_reason=response.finish_reason.value,
                    tool_call_count=len(response.message.tool_calls),
                )
            ],
        )
        self.state = next_state
        self.trace = next_trace

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        self.state = update_state(
            self.state,
            policy_decisions=[*self.state.policy_decisions, decision],
        )

    async def complete_tool_step(
        self,
        results: list[ToolResult],
    ) -> RunTermination | None:
        failed = tool_step_failed(results)
        next_state = update_state(
            self.state,
            pending_tool_calls=[],
            cursor=RunCursor.REQUEST_MODEL,
            consecutive_failed_steps=(
                self.state.consecutive_failed_steps + 1 if failed else 0
            ),
        )
        next_trace = deepcopy(self.trace)
        await self.journal.complete_step(
            next_state,
            [
                next_trace.complete_step(
                    {
                        "failed": failed,
                        "consecutive_failed_steps": (
                            next_state.consecutive_failed_steps
                        ),
                    }
                )
            ],
        )
        self.state = next_state
        self.trace = next_trace
        if (
            next_state.consecutive_failed_steps
            < next_state.config.max_consecutive_failures
        ):
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
        )

    async def begin_evaluation(self, candidate_output: str) -> None:
        next_state = update_state(self.state, candidate_output=candidate_output)
        next_state = transition(next_state, RunStatus.EVALUATING)
        next_trace = deepcopy(self.trace)
        await self.journal.complete_step(
            next_state,
            [next_trace.complete_step({"failed": False, "candidate_output": True})],
        )
        self.state = next_state
        self.trace = next_trace

    def stage_evaluation_result(
        self,
        *,
        passed: bool,
        final_output: str | None = None,
    ) -> None:
        self.pending_evaluation_result = _PendingEvaluationResult(
            passed=passed,
            final_output=final_output,
        )

    async def begin_tool(
        self,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        next_trace = deepcopy(self.trace)
        await self.journal.record_activity(
            self.state,
            [
                next_trace.begin_tool(
                    call_index=call_index,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
            ],
        )
        self.trace = next_trace

    async def begin_tool_execution(self) -> None:
        next_state = update_state(
            self.state,
            usage=self.state.usage.model_copy(
                update={"tool_calls": self.state.usage.tool_calls + 1}
            ),
        )
        next_trace = deepcopy(self.trace)
        await self.journal.record_activity(
            next_state,
            [next_trace.begin_tool_execution()],
        )
        self.state = next_state
        self.trace = next_trace

    async def complete_tool(self, result: ToolResult) -> None:
        message = Message(
            role="tool",
            tool_call_id=result.tool_call_id,
            content=render_tool_result(
                result,
                self.state.config.tool_output_limits,
            ),
        )
        next_state = update_state(
            self.state,
            messages=[*self.state.messages, message],
            completed_tool_call_ids={
                *self.state.completed_tool_call_ids,
                result.tool_call_id,
            },
        )
        payload: dict[str, JsonValue] = {
            "status": result.status.value,
            "error_code": result.error.code if result.error is not None else None,
        }
        next_trace = deepcopy(self.trace)
        await self.journal.record_activity(
            next_state,
            next_trace.complete_tool(payload),
        )
        self.state = next_state
        self.trace = next_trace

    async def open_approval(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> None:
        next_state = update_state(
            self.state,
            approval_requests=[*self.state.approval_requests, request],
        )
        next_state = transition(
            next_state,
            RunStatus.WAITING_APPROVAL,
            pending_approval_id=str(request.approval_id),
        )
        next_trace = deepcopy(self.trace)
        next_trace.begin_approval()
        await self.journal.open_approval(
            next_state,
            [
                next_trace.approval_requested(
                    approval_id=str(request.approval_id),
                    decision_id=str(decision.decision_id),
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
        next_state = update_state(
            self.state,
            approval_resolutions=[*self.state.approval_resolutions, resolution],
        )
        next_state = transition(
            next_state,
            RunStatus.RUNNING,
            cursor=RunCursor.EXECUTE_TOOL_CALLS,
            pending_approval_id=None,
        )
        next_trace = deepcopy(self.trace)
        await self.journal.resolve_approval(
            next_state,
            [
                next_trace.approval_resolved(
                    approval_id=str(resolution.approval_id),
                    outcome=resolution.outcome.value,
                )
            ],
            resolution,
        )
        next_trace.complete_approval()
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
                self.trace.approval_invalidated(
                    approval_id=str(approval_id),
                    reason=reason,
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
        pending = self.pending_approval_resolution
        if (
            termination.status is RunStatus.CANCELLED
            and self.state.pending_approval_id is not None
            and pending is None
        ):
            resolution = ApprovalResolution(
                approval_id=UUID(self.state.pending_approval_id),
                outcome=ApprovalOutcome.INVALIDATED,
                actor_kind="system",
                actor_id="runtime",
                reason="The run was cancelled.",
                resolved_at=datetime.now(UTC),
            )
            pending = _PendingApprovalResolution(resolution=resolution)

        changes: dict[str, object] = {}
        if pending is not None:
            changes["approval_resolutions"] = [
                *self.state.approval_resolutions,
                pending.resolution,
            ]
        evaluation = self.pending_evaluation_result
        if evaluation is not None:
            changes["final_output"] = evaluation.final_output
        next_state = update_state(self.state, **changes)
        next_state = transition(
            next_state,
            termination.status,
            reason=termination.reason,
            error=termination.error,
            pending_approval_id=None,
        )
        next_trace = deepcopy(self.trace)
        events = []
        if pending is not None:
            events.append(
                next_trace.approval_resolved(
                    approval_id=str(pending.resolution.approval_id),
                    outcome=pending.resolution.outcome.value,
                )
            )
        if evaluation is not None:
            events.append(
                next_trace.evaluation_completed(passed=evaluation.passed)
            )
        events.extend(
            next_trace.finish(
                status=next_state.status,
                reason=termination.reason,
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
