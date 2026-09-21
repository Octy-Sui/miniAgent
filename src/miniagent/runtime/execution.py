from collections.abc import Sequence
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
    validate_state,
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
class RunExecution:
    state: RunState
    journal: RunJournal
    trace: RunTraceContext
    pending_approval_resolution: _PendingApprovalResolution | None = None
    evaluation_passed: bool | None = None

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
        transition(self.state, RunStatus.PREPARING)
        transition(self.state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)
        await self.journal.start_run(
            self.state,
            [self.trace.run_started()],
        )

    def before_next_step(self) -> RunTermination | None:
        if self.state.steps_started < self.state.config.max_steps:
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.MAX_STEPS_REACHED,
        )

    async def begin_step(self, *, provider: str, model: str) -> None:
        self.state.steps_started += 1
        self.state.usage.model_requests += 1
        await self.journal.record_activity(
            self.state,
            self.trace.begin_step(
                number=self.state.steps_started,
                provider=provider,
                model=model,
            ),
        )

    async def record_model_response(
        self,
        response: ModelResponse,
        *,
        pending_tool_calls: Sequence[ToolCall] | None = None,
    ) -> None:
        if pending_tool_calls is not None:
            self.state.pending_tool_calls = list(pending_tool_calls)
            self.state.cursor = RunCursor.EXECUTE_TOOL_CALLS
            validate_state(self.state)
        await self.journal.record_activity(
            self.state,
            [
                self.trace.model_responded(
                    finish_reason=response.finish_reason.value,
                    tool_call_count=len(response.message.tool_calls),
                )
            ],
        )

    def accept_model_response(
        self,
        response: ModelResponse,
        request_cost: RequestCost | None,
    ) -> None:
        if response.usage is not None:
            self.state.usage = add_request_usage(
                self.state.usage,
                response.usage,
                request_cost,
            )
        self.state.messages.append(response.message)

    def record_policy_decision(self, decision: PolicyDecision) -> None:
        self.state.policy_decisions.append(decision)

    async def complete_tool_step(
        self,
        results: list[ToolResult],
    ) -> RunTermination | None:
        failed = tool_step_failed(results)
        self.state.pending_tool_calls = []
        self.state.cursor = RunCursor.REQUEST_MODEL
        if failed:
            self.state.consecutive_failed_steps += 1
        else:
            self.state.consecutive_failed_steps = 0
        validate_state(self.state)
        await self.journal.complete_step(
            self.state,
            [
                self.trace.complete_step(
                    {
                        "failed": failed,
                        "consecutive_failed_steps": (
                            self.state.consecutive_failed_steps
                        ),
                    }
                )
            ],
        )
        if (
            self.state.consecutive_failed_steps
            < self.state.config.max_consecutive_failures
        ):
            return None
        return RunTermination(
            status=RunStatus.FAILED,
            reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
        )

    async def begin_evaluation(self, candidate_output: str) -> None:
        self.state.candidate_output = candidate_output
        transition(self.state, RunStatus.EVALUATING)
        await self.journal.complete_step(
            self.state,
            [self.trace.complete_step({"failed": False, "candidate_output": True})],
        )

    def stage_evaluation_result(
        self,
        *,
        passed: bool,
        final_output: str | None = None,
    ) -> None:
        self.evaluation_passed = passed
        self.state.final_output = final_output

    async def begin_tool(
        self,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        await self.journal.record_activity(
            self.state,
            [
                self.trace.begin_tool(
                    call_index=call_index,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                )
            ],
        )

    async def begin_tool_execution(self) -> None:
        self.state.usage.tool_calls += 1
        await self.journal.record_activity(
            self.state,
            [self.trace.begin_tool_execution()],
        )

    async def complete_tool(self, result: ToolResult) -> None:
        self.state.messages.append(
            Message(
                role="tool",
                tool_call_id=result.tool_call_id,
                content=render_tool_result(
                    result,
                    self.state.config.tool_output_limits,
                ),
            )
        )
        self.state.completed_tool_call_ids.add(result.tool_call_id)
        payload: dict[str, JsonValue] = {
            "status": result.status.value,
            "error_code": result.error.code if result.error is not None else None,
        }
        await self.journal.record_activity(
            self.state,
            self.trace.complete_tool(payload),
        )

    async def open_approval(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> None:
        self.trace.begin_approval()
        self.state.approval_requests.append(request)
        transition(
            self.state,
            RunStatus.WAITING_APPROVAL,
            pending_approval_id=str(request.approval_id),
        )
        await self.journal.open_approval(
            self.state,
            [
                self.trace.approval_requested(
                    approval_id=str(request.approval_id),
                    decision_id=str(decision.decision_id),
                )
            ],
            request=request,
            decision=decision,
        )

    async def resolve_approval(
        self,
        resolution: ApprovalResolution,
    ) -> None:
        self.state.approval_resolutions.append(resolution)
        transition(
            self.state,
            RunStatus.RUNNING,
            cursor=RunCursor.EXECUTE_TOOL_CALLS,
            pending_approval_id=None,
        )
        await self.journal.resolve_approval(
            self.state,
            [
                self.trace.approval_resolved(
                    approval_id=str(resolution.approval_id),
                    outcome=resolution.outcome.value,
                )
            ],
            resolution,
        )
        self.trace.complete_approval()

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

    def replace_state(self, state: RunState) -> None:
        self.state = state

    def stage_approval_resolution(
        self,
        resolution: ApprovalResolution,
    ) -> None:
        self.state.approval_resolutions.append(resolution)
        self.pending_approval_resolution = _PendingApprovalResolution(
            resolution=resolution,
        )

    async def finish(
        self,
        termination: RunTermination,
    ) -> RunResult:
        if (
            termination.status is RunStatus.CANCELLED
            and self.state.pending_approval_id is not None
            and self.pending_approval_resolution is None
        ):
            resolution = ApprovalResolution(
                approval_id=UUID(self.state.pending_approval_id),
                outcome=ApprovalOutcome.INVALIDATED,
                actor_kind="system",
                actor_id="runtime",
                reason="The run was cancelled.",
                resolved_at=datetime.now(UTC),
            )
            self.stage_approval_resolution(resolution)

        transition(
            self.state,
            termination.status,
            reason=termination.reason,
            error=termination.error,
            pending_approval_id=None,
        )
        events = []
        pending = self.pending_approval_resolution
        if pending is not None:
            events.append(
                self.trace.approval_resolved(
                    approval_id=str(pending.resolution.approval_id),
                    outcome=pending.resolution.outcome.value,
                )
            )
        if self.evaluation_passed is not None:
            events.append(
                self.trace.evaluation_completed(passed=self.evaluation_passed)
            )
        events.extend(
            self.trace.finish(
                status=self.state.status,
                reason=termination.reason,
            )
        )
        await self.journal.finish_run(
            self.state,
            events,
            approval_resolution=(pending.resolution if pending is not None else None),
        )
        self.pending_approval_resolution = None
        self.evaluation_passed = None
        return RunResult.from_state(self.state)
