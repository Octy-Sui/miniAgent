from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from miniagent.agents import AgentSpec
from miniagent.metering.usage import RunUsage
from miniagent.models.messages import Message
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.result import RunError, RunResult, TerminationReason
from miniagent.tools.contracts import ToolCall, ToolDefinition


class RunStatus(StrEnum):
    CREATED = "created"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    RECOVERING = "recovering"
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


class ConversationState(_StatePart):
    messages: tuple[Message, ...] = ()


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
    checkpoint_id: str | None = None
    attempts: int = Field(default=0, ge=0)


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: RunIdentity
    definition: RunDefinition
    lifecycle: RunLifecycle
    conversation: ConversationState = Field(default_factory=ConversationState)
    execution: ExecutionProgress = Field(default_factory=ExecutionProgress)
    approval: ApprovalProgress = Field(default_factory=ApprovalProgress)
    recovery: RecoveryProgress = Field(default_factory=RecoveryProgress)
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

    if lifecycle.status is RunStatus.RUNNING and execution.cursor is None:
        raise ValueError("Running state requires a cursor")
    if lifecycle.status is not RunStatus.RUNNING and execution.cursor is not None:
        raise ValueError("Only running state may have a cursor")
    if (
        execution.cursor is RunCursor.EXECUTE_TOOL_CALLS
        and execution.tool_batch is None
    ):
        raise ValueError("Tool execution cursor requires a tool batch")
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
