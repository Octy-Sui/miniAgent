from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from miniagent.agents import AgentSpec
from miniagent.metering.usage import RunUsage
from miniagent.models.messages import Message
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.progress import (
    CurrentStep,
    EvaluationProgress,
    ExecutionPhase,
    InterruptionInfo,
    OperationRef,
    RecoverySession,
    RequestProgress,
    ResponseDisposition,
    SuspensionMetadata,
)
from miniagent.runtime.result import (
    RunError,
    RunResult,
    RunSuspension,
    TerminationReason,
)
from miniagent.tools.contracts import ToolCall, ToolDefinition


class RunStatus(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RECOVERING = "recovering"
    SUSPENDED = "suspended"
    EVALUATING = "evaluating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunCursor(StrEnum):
    REQUEST_MODEL = "request_model"
    EXECUTE_TOOL_CALLS = "execute_tool_calls"


TERMINAL_STATUSES = frozenset(
    (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)
)


class _StatePart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunIdentity(_StatePart):
    run_id: UUID
    task: str
    created_at: datetime


class RunDefinition(_StatePart):
    agent_spec: AgentSpec
    config: RuntimeConfig
    tool_definitions: tuple[ToolDefinition, ...] = ()
    tool_contract_metadata: tuple[dict[str, JsonValue], ...] = ()


class RunLifecycle(_StatePart):
    status: RunStatus = RunStatus.CREATED
    transition_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None
    candidate_output: str | None = None
    final_output: str | None = None
    termination_reason: TerminationReason | None = None
    error: RunError | None = None


class StepRange(_StatePart):
    step_number: int = Field(ge=1)
    message_start: int = Field(ge=0)
    message_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> "StepRange":
        if self.message_end <= self.message_start:
            raise ValueError("Step message range must be nonempty")
        return self


class ConversationState(_StatePart):
    messages: tuple[Message, ...] = ()
    completed_steps: tuple[StepRange, ...] = ()


class ToolBatch(_StatePart):
    calls: tuple[ToolCall, ...] = Field(min_length=1)
    next_index: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_batch(self) -> "ToolBatch":
        if self.next_index > len(self.calls):
            raise ValueError("Tool batch cursor exceeds its call count")
        call_ids = [call.id for call in self.calls]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Tool call IDs must be unique within a step")
        return self


class ExecutionProgress(_StatePart):
    phase: ExecutionPhase | None = ExecutionPhase.PREPARE
    current_step: CurrentStep | None = None
    request_progress: RequestProgress = Field(default_factory=RequestProgress)
    active_operation: OperationRef | None = None
    response_disposition: ResponseDisposition | None = None
    evaluation: EvaluationProgress = Field(default_factory=EvaluationProgress)
    cursor: RunCursor | None = None
    steps_started: int = Field(default=0, ge=0)
    consecutive_failed_steps: int = Field(default=0, ge=0)
    tool_batch: ToolBatch | None = None


class PendingApprovalRef(_StatePart):
    approval_id: UUID
    step_number: int = Field(ge=1)
    call_index: int = Field(ge=0)
    tool_call_id: str


class ApprovalProgress(_StatePart):
    pending: PendingApprovalRef | None = None


class RecoveryProgress(_StatePart):
    resume_attempts: int = Field(default=0, ge=0)
    suspension: SuspensionMetadata | None = None
    session: RecoverySession | None = None
    checkpoint_id: str | None = None
    attempts: int = Field(default=0, ge=0)


class ContextProgress(_StatePart):
    current_version: int | None = Field(default=None, ge=1)


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: RunIdentity
    definition: RunDefinition
    lifecycle: RunLifecycle
    conversation: ConversationState = Field(default_factory=ConversationState)
    execution: ExecutionProgress = Field(default_factory=ExecutionProgress)
    approval: ApprovalProgress = Field(default_factory=ApprovalProgress)
    recovery: RecoveryProgress = Field(default_factory=RecoveryProgress)
    context: ContextProgress = Field(default_factory=ContextProgress)
    usage: RunUsage = Field(default_factory=RunUsage)

    @model_validator(mode="after")
    def validate_invariants(self) -> "RunState":
        validate_state(self)
        return self


class InvalidRunTransition(ValueError):
    pass


def create_run_state(
    *,
    run_id: UUID,
    task: str,
    agent_spec: AgentSpec,
    config: RuntimeConfig,
    messages: tuple[Message, ...] = (),
    created_at: datetime | None = None,
) -> RunState:
    now = created_at or datetime.now(UTC)
    return RunState(
        identity=RunIdentity(run_id=run_id, task=task, created_at=now),
        definition=RunDefinition(
            agent_spec=agent_spec.model_copy(deep=True),
            config=config.model_copy(deep=True),
        ),
        lifecycle=RunLifecycle(updated_at=now),
        conversation=ConversationState(
            messages=tuple(message.model_copy(deep=True) for message in messages)
        ),
    )


def validate_state(state: RunState) -> None:
    lifecycle = state.lifecycle
    execution = state.execution
    is_terminal = lifecycle.status in TERMINAL_STATUSES

    if is_terminal:
        if execution.phase is not None:
            raise ValueError("Terminal state cannot have an execution phase")
        if execution.active_operation is not None:
            raise ValueError("Terminal state cannot have an active operation")
        if execution.current_step is not None or execution.tool_batch is not None:
            raise ValueError("Terminal state cannot have current_step or tool batch")
    elif execution.phase is None:
        raise ValueError("Non-terminal state requires an execution phase")
    if lifecycle.status is RunStatus.RUNNING:
        expected_cursor = (
            RunCursor.EXECUTE_TOOL_CALLS
            if execution.phase
            in (ExecutionPhase.EXECUTE_TOOLS, ExecutionPhase.COMPLETE_STEP)
            else RunCursor.REQUEST_MODEL
        )
        if execution.cursor is not expected_cursor:
            raise ValueError("Running cursor must match execution phase")
    elif execution.cursor is not None:
        raise ValueError("Only running state may have a cursor")
    batch = execution.tool_batch
    if execution.phase in (ExecutionPhase.EXECUTE_TOOLS, ExecutionPhase.COMPLETE_STEP):
        if batch is None or execution.current_step is None:
            raise ValueError("Tool phase requires tool batch and current_step")
        if execution.phase is ExecutionPhase.EXECUTE_TOOLS and batch.next_index >= len(
            batch.calls
        ):
            raise ValueError("execute_tools cursor must point to an unfinished call")
        if execution.phase is ExecutionPhase.COMPLETE_STEP and batch.next_index != len(
            batch.calls
        ):
            raise ValueError("complete_step requires a completed batch")
    elif batch is not None:
        raise ValueError("Tool batch requires a tool execution phase")
    step = execution.current_step
    if step is not None:
        if step.step_number != execution.steps_started:
            raise ValueError("current_step must match steps_started")
        if any(
            completed.step_number == step.step_number
            for completed in state.conversation.completed_steps
        ):
            raise ValueError("Completed Step cannot retain current_step")
    operation = execution.active_operation
    if operation is not None:
        if operation.kind in ("tool", "model") and (
            step is None or operation.step_number != step.step_number
        ):
            raise ValueError("Active operation must match current_step")
        if operation.kind == "tool" and (
            batch is None
            or batch.next_index >= len(batch.calls)
            or operation.call_index != batch.next_index
            or operation.tool_call_id != batch.calls[batch.next_index].id
        ):
            raise ValueError("Tool operation must match current batch call")
    disposition = execution.response_disposition
    if execution.phase is ExecutionPhase.TERMINATE and (
        disposition is None or disposition.kind != "terminate"
    ):
        raise ValueError("terminate phase requires a termination decision")
    if execution.phase is ExecutionPhase.EVALUATE_CANDIDATE:
        if disposition is None or disposition.kind != "candidate":
            raise ValueError("Candidate phase requires original candidate text")
        if lifecycle.candidate_output != disposition.text:
            raise ValueError("Candidate text must match lifecycle candidate_output")
    if (
        execution.request_progress.summary_retry_used
        > state.definition.config.compaction_max_retries
    ):
        raise ValueError("summary_retry_used exceeds frozen compaction_max_retries")
    if lifecycle.status is RunStatus.SUSPENDED and state.recovery.suspension is None:
        raise ValueError("Suspended state requires suspension business source")
    if lifecycle.status is RunStatus.RECOVERING and state.recovery.session is None:
        raise ValueError("Recovering state requires recovery session business source")
    if lifecycle.status is RunStatus.WAITING_APPROVAL:
        if state.approval.pending is None:
            raise ValueError("Waiting approval state requires a pending approval")
    elif state.approval.pending is not None:
        raise ValueError("Pending approval requires waiting approval state")
    if lifecycle.status is RunStatus.EVALUATING and not lifecycle.candidate_output:
        raise ValueError("Evaluating state requires candidate output")
    if is_terminal:
        if lifecycle.termination_reason is None or lifecycle.finished_at is None:
            raise ValueError("Terminal state requires termination details")
    elif lifecycle.termination_reason is not None or lifecycle.finished_at is not None:
        raise ValueError("Non-terminal state cannot have termination details")
    if lifecycle.status is RunStatus.SUCCEEDED and not lifecycle.final_output:
        raise ValueError("Succeeded state requires final output")


RunResult.model_rebuild(_types_namespace={"RunStatus": RunStatus})

RunSuspension.model_rebuild(
    _types_namespace={"RunStatus": RunStatus, "InterruptionInfo": InterruptionInfo}
)
