from datetime import UTC, datetime

from miniagent.metering.usage import RequestCost, add_request_usage
from miniagent.models.messages import Message
from miniagent.models.protocol import ModelResponse
from miniagent.policy import ApprovalRequest, ApprovalResolution, PolicyDecision
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.runtime.state import (
    TERMINAL_STATUSES,
    ApprovalProgress,
    ConversationState,
    InvalidRunTransition,
    PendingApprovalRef,
    RunCursor,
    RunLifecycle,
    RunState,
    RunStatus,
    ToolBatch,
    StepRange,
)
from miniagent.tools.contracts import ToolCall

_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset((RunStatus.PREPARING, RunStatus.CANCELLED)),
    RunStatus.PREPARING: frozenset(
        (RunStatus.RUNNING, RunStatus.RECOVERING, RunStatus.FAILED, RunStatus.CANCELLED)
    ),
    RunStatus.RUNNING: frozenset(
        (
            RunStatus.WAITING_APPROVAL,
            RunStatus.RECOVERING,
            RunStatus.EVALUATING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        (RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED)
    ),
    RunStatus.RECOVERING: frozenset(
        (RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED)
    ),
    RunStatus.EVALUATING: frozenset(
        (
            RunStatus.SUCCEEDED,
            RunStatus.RUNNING,
            RunStatus.RECOVERING,
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
    return _transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)


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
    response: ModelResponse,
    request_cost: RequestCost | None,
    *,
    tool_calls: tuple[ToolCall, ...] | None = None,
) -> RunState:
    usage = state.usage
    if response.usage is not None:
        usage = add_request_usage(usage, response.usage, request_cost)
    conversation = state.conversation.model_copy(update={"messages": (*state.conversation.messages, response.message)})
    execution = state.execution
    if tool_calls is not None:
        execution = execution.model_copy(
            update={
                "tool_batch": ToolBatch(calls=tool_calls),
                "cursor": RunCursor.EXECUTE_TOOL_CALLS,
            }
        )
    return _replace(
        state,
        conversation=conversation,
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
        conversation=state.conversation.model_copy(update={"messages": (*state.conversation.messages, message)}),
        execution=state.execution.model_copy(
            update={
                "tool_batch": batch.model_copy(
                    update={"next_index": batch.next_index + 1}
                )
            }
        ),
    )


def complete_tool_step(state: RunState, *, failed: bool) -> RunState:
    batch = state.execution.tool_batch
    if batch is None or batch.next_index != len(batch.calls):
        raise ValueError("Tool step cannot finish before every call completes")
    end = len(state.conversation.messages)
    start = end - len(batch.calls) - 1
    completed = StepRange(step_number=state.execution.steps_started, message_start=start, message_end=end)
    return _replace(
        state,
        conversation=state.conversation.model_copy(update={"completed_steps": (*state.conversation.completed_steps, completed)}),
        execution=state.execution.model_copy(
            update={
                "tool_batch": None,
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
        _replace(state, lifecycle=lifecycle),
        RunStatus.EVALUATING,
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
    execution = state.execution.model_copy(update={"cursor": cursor})
    return _replace(
        state,
        lifecycle=lifecycle,
        execution=execution,
        approval=approval or state.approval,
    )


def _replace(state: RunState, **parts: object) -> RunState:
    candidate = state.model_copy(deep=True, update=parts)
    return RunState.model_validate(candidate.model_dump(mode="python"))
