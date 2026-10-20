from datetime import UTC, datetime
from uuid import UUID

from miniagent.metering.usage import RequestCost, add_request_usage
from miniagent.models.messages import Message
from miniagent.models.protocol import ModelResponse
from miniagent.policy import ApprovalRequest, ApprovalResolution, PolicyDecision
from miniagent.runtime.progress import (
    CandidateDisposition,
    ExecutionPhase,
    InterruptionInfo,
    RecoverySession,
    RequestProgress,
    ResponseDisposition,
    SuspensionMetadata,
    TerminateDisposition,
    ToolsDisposition,
)
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.runtime.state import (
    TERMINAL_STATUSES,
    ApprovalProgress,
    ConversationState,
    ExecutionProgress,
    InvalidRunTransition,
    PendingApprovalRef,
    RecoveryProgress,
    RunCursor,
    RunLifecycle,
    RunState,
    RunStatus,
    StepRange,
    ToolBatch,
)

_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        (
            RunStatus.PREPARING,
            RunStatus.CANCELLED,
            RunStatus.SUSPENDED,
            RunStatus.RECOVERING,
        )
    ),
    RunStatus.PREPARING: frozenset(
        (
            RunStatus.RUNNING,
            RunStatus.RECOVERING,
            RunStatus.SUSPENDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.RUNNING: frozenset(
        (
            RunStatus.WAITING_APPROVAL,
            RunStatus.RECOVERING,
            RunStatus.SUSPENDED,
            RunStatus.EVALUATING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        (
            RunStatus.RUNNING,
            RunStatus.RECOVERING,
            RunStatus.SUSPENDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.RECOVERING: frozenset(
        (
            RunStatus.RECOVERING,
            RunStatus.SUSPENDED,
            RunStatus.PREPARING,
            RunStatus.RUNNING,
            RunStatus.EVALUATING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.SUSPENDED: frozenset((RunStatus.RECOVERING,)),
    RunStatus.EVALUATING: frozenset(
        (
            RunStatus.SUCCEEDED,
            RunStatus.RUNNING,
            RunStatus.RECOVERING,
            RunStatus.SUSPENDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def prepare(state: RunState) -> RunState:
    return _transition(state, RunStatus.PREPARING)


def start_running(state: RunState) -> RunState:
    return _transition(
        state,
        RunStatus.RUNNING,
        cursor=RunCursor.REQUEST_MODEL,
        execution=state.execution.model_copy(
            update={"phase": ExecutionPhase.REQUEST_MODEL}
        ),
    )


def begin_step(state: RunState) -> RunState:
    execution = state.execution.model_copy(
        update={"steps_started": state.execution.steps_started + 1}
    )
    usage = state.usage.model_copy(
        update={"model_requests": state.usage.model_requests + 1}
    )
    return _replace(state, execution=execution, usage=usage)


def accept_model_response(
    state: RunState,
    response: ModelResponse | None,
    request_cost: RequestCost | None,
    *,
    disposition: ResponseDisposition,
) -> RunState:
    usage = state.usage
    if response is not None and response.usage is not None:
        usage = add_request_usage(usage, response.usage, request_cost)
    conversation = state.conversation.model_copy(
        update={
            "messages": (*state.conversation.messages, response.message)
            if response is not None
            else state.conversation.messages
        }
    )
    changes: dict[str, object] = {
        "response_disposition": disposition,
        "active_operation": None,
    }
    lifecycle = state.lifecycle
    if isinstance(disposition, ToolsDisposition):
        assert response is not None
        changes.update(
            tool_batch=ToolBatch(calls=response.message.tool_calls),
            cursor=RunCursor.EXECUTE_TOOL_CALLS,
            phase=ExecutionPhase.EXECUTE_TOOLS,
        )
    elif isinstance(disposition, CandidateDisposition):
        changes.update(phase=ExecutionPhase.EVALUATE_CANDIDATE)
        lifecycle = lifecycle.model_copy(update={"candidate_output": disposition.text})
    elif isinstance(disposition, TerminateDisposition):
        changes.update(phase=ExecutionPhase.TERMINATE)
    execution = state.execution.model_copy(update=changes)
    return _replace(
        state,
        conversation=conversation,
        lifecycle=lifecycle,
        execution=execution,
        usage=usage,
    )


def begin_tool_execution(state: RunState) -> RunState:
    return _replace(
        state,
        usage=state.usage.model_copy(update={"tool_calls": state.usage.tool_calls + 1}),
    )


def complete_tool(
    state: RunState,
    *,
    message: Message,
    tool_call_id: str,
) -> RunState:
    batch = state.execution.tool_batch
    if batch is None or batch.next_index >= len(batch.calls):
        raise ValueError("No current tool call is available to complete")
    if batch.calls[batch.next_index].id != tool_call_id:
        raise ValueError("Completed tool result does not match the batch cursor")
    return _replace(
        state,
        conversation=state.conversation.model_copy(
            update={"messages": (*state.conversation.messages, message)}
        ),
        execution=state.execution.model_copy(
            update={
                "tool_batch": batch.model_copy(
                    update={"next_index": batch.next_index + 1}
                ),
                "phase": ExecutionPhase.COMPLETE_STEP
                if batch.next_index + 1 == len(batch.calls)
                else ExecutionPhase.EXECUTE_TOOLS,
                "active_operation": None,
            }
        ),
    )


def complete_tool_step(state: RunState, *, failed: bool) -> RunState:
    batch = state.execution.tool_batch
    if batch is None or batch.next_index != len(batch.calls):
        raise ValueError("Tool step cannot finish before every call completes")
    end = len(state.conversation.messages)
    start = end - len(batch.calls) - 1
    completed = StepRange(
        step_number=state.execution.steps_started, message_start=start, message_end=end
    )
    return _replace(
        state,
        conversation=state.conversation.model_copy(
            update={"completed_steps": (*state.conversation.completed_steps, completed)}
        ),
        execution=state.execution.model_copy(
            update={
                "tool_batch": None,
                "current_step": None,
                "response_disposition": None,
                "request_progress": RequestProgress(),
                "phase": ExecutionPhase.REQUEST_MODEL,
                "cursor": RunCursor.REQUEST_MODEL,
                "consecutive_failed_steps": (
                    state.execution.consecutive_failed_steps + 1 if failed else 0
                ),
            }
        ),
    )


def wait_for_approval(
    state: RunState,
    request: ApprovalRequest,
    decision: PolicyDecision,
) -> RunState:
    policy_request = decision.request
    approval = state.approval.model_copy(
        update={
            "pending": PendingApprovalRef(
                approval_id=request.approval_id,
                step_number=policy_request.step_number,
                call_index=policy_request.call_index,
                tool_call_id=policy_request.tool_call_id,
            ),
        }
    )
    return _transition(
        state,
        RunStatus.WAITING_APPROVAL,
        approval=approval,
    )


def resolve_approval(
    state: RunState,
    resolution: ApprovalResolution,
) -> RunState:
    approval = state.approval.model_copy(
        update={
            "pending": None,
        }
    )
    return _transition(
        state,
        RunStatus.RUNNING,
        cursor=RunCursor.EXECUTE_TOOL_CALLS,
        approval=approval,
    )


def begin_evaluation(state: RunState, candidate_output: str) -> RunState:
    lifecycle = state.lifecycle.model_copy(
        update={"candidate_output": candidate_output}
    )
    return _transition(
        state,
        RunStatus.EVALUATING,
        lifecycle=lifecycle,
        execution=state.execution.model_copy(
            update={
                "phase": ExecutionPhase.EVALUATE_CANDIDATE,
                "response_disposition": CandidateDisposition(text=candidate_output),
                "tool_batch": None,
                "current_step": None,
                "active_operation": None,
            }
        ),
    )


def finish(
    state: RunState,
    status: RunStatus,
    reason: TerminationReason,
    *,
    error: RunError | None = None,
    approval_resolution: ApprovalResolution | None = None,
    final_output: str | None = None,
) -> RunState:
    approval = state.approval
    if approval_resolution is not None or approval.pending is not None:
        approval = approval.model_copy(update={"pending": None})
    lifecycle = state.lifecycle.model_copy(update={"final_output": final_output})
    return _transition(
        state,
        status,
        reason=reason,
        error=error,
        approval=approval,
        lifecycle=lifecycle,
    )


def _business_source(state: RunState) -> str:
    if state.lifecycle.status is RunStatus.RECOVERING:
        if state.recovery.session is None:
            raise ValueError("Recovering state requires session")
        return state.recovery.session.business_status
    if state.lifecycle.status is RunStatus.SUSPENDED:
        if state.recovery.suspension is None:
            raise ValueError("Suspended state requires suspension")
        return state.recovery.suspension.business_status
    return state.lifecycle.status.value


def suspend(state: RunState, interruption: InterruptionInfo) -> RunState:
    if (
        state.lifecycle.status in TERMINAL_STATUSES
        or state.lifecycle.status is RunStatus.SUSPENDED
    ):
        raise InvalidRunTransition("Cannot suspend terminal or suspended state")
    metadata = SuspensionMetadata.model_validate(
        {
            "interruption": interruption,
            "business_status": _business_source(state),
            "suspended_at": datetime.now(UTC),
        }
    )
    return _transition(
        state,
        RunStatus.SUSPENDED,
        approval=ApprovalProgress(),
        recovery=state.recovery.model_copy(
            update={"suspension": metadata, "session": None}
        ),
    )


def begin_resume(
    state: RunState, *, attempt_id: UUID, source_sequence: int
) -> RunState:
    if state.lifecycle.status in TERMINAL_STATUSES:
        raise InvalidRunTransition("Cannot resume terminal state")
    session = RecoverySession.model_validate(
        {
            "attempt_id": attempt_id,
            "source_sequence": source_sequence,
            "business_status": _business_source(state),
            "phase_at_start": state.execution.phase,
        }
    )
    return _transition(
        state,
        RunStatus.RECOVERING,
        approval=ApprovalProgress(),
        recovery=state.recovery.model_copy(
            update={
                "resume_attempts": state.recovery.resume_attempts + 1,
                "session": session,
            }
        ),
    )


def complete_resume(state: RunState) -> RunState:
    session = state.recovery.session
    if state.lifecycle.status is not RunStatus.RECOVERING or session is None:
        raise InvalidRunTransition("complete_resume requires recovering session")
    target = RunStatus(session.business_status)
    if target is RunStatus.CREATED:
        target = RunStatus.PREPARING
    if target is RunStatus.WAITING_APPROVAL:
        target = RunStatus.RUNNING
    cursor = None
    if target is RunStatus.RUNNING:
        cursor = (
            RunCursor.EXECUTE_TOOL_CALLS
            if state.execution.phase
            in (ExecutionPhase.EXECUTE_TOOLS, ExecutionPhase.COMPLETE_STEP)
            else RunCursor.REQUEST_MODEL
        )
    return _transition(
        state,
        target,
        cursor=cursor,
        recovery=state.recovery.model_copy(
            update={"session": None, "suspension": None}
        ),
    )


def replace_conversation(
    state: RunState,
    messages: tuple[Message, ...],
) -> RunState:
    return _replace(state, conversation=ConversationState(messages=messages))


def _transition(
    state: RunState,
    target: RunStatus,
    *,
    cursor: RunCursor | None = None,
    reason: TerminationReason | None = None,
    error: RunError | None = None,
    approval: ApprovalProgress | None = None,
    lifecycle: RunLifecycle | None = None,
    execution: ExecutionProgress | None = None,
    recovery: RecoveryProgress | None = None,
) -> RunState:
    lifecycle = lifecycle or state.lifecycle
    current = lifecycle.status
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidRunTransition(
            f"Cannot transition run {state.identity.run_id} from {current} to {target}"
        )
    is_terminal = target in TERMINAL_STATUSES
    if is_terminal and reason is None:
        raise ValueError("Terminal transition requires a termination reason")
    if not is_terminal and (reason is not None or error is not None):
        raise ValueError("Non-terminal transition cannot include termination details")

    now = datetime.now(UTC)
    changes: dict[str, object] = {
        "status": target,
        "updated_at": now,
        "transition_count": state.lifecycle.transition_count + 1,
    }
    if lifecycle.started_at is None and target is RunStatus.PREPARING:
        changes["started_at"] = now
    if is_terminal:
        changes.update(
            termination_reason=reason,
            error=error,
            finished_at=now,
        )
    lifecycle = lifecycle.model_copy(update=changes)
    execution = (execution or state.execution).model_copy(update={"cursor": cursor})
    if is_terminal:
        execution = execution.model_copy(
            update={
                "phase": None,
                "active_operation": None,
                "current_step": None,
                "tool_batch": None,
                "response_disposition": None,
            }
        )
    return _replace(
        state,
        lifecycle=lifecycle,
        execution=execution,
        approval=approval or state.approval,
        recovery=recovery or state.recovery,
    )


def _replace(state: RunState, **parts: object) -> RunState:
    candidate = state.model_copy(deep=True, update=parts)
    return RunState.model_validate(candidate.model_dump(mode="python"))
