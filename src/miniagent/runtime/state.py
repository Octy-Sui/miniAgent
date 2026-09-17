# File   : state.py
# Created: 2026-09-10 16:39:36
# Author : octy Sui

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.agents import AgentSpec
from miniagent.models.model import Message
from miniagent.models.tool import ToolCall, ToolOutputLimits


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
    (
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    )
)


ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset(
        (
            RunStatus.PREPARING,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.PREPARING: frozenset(
        (
            RunStatus.RUNNING,
            RunStatus.RECOVERING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
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
        (
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
    ),
    RunStatus.RECOVERING: frozenset(
        (
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        )
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


class TerminationReason(StrEnum):
    TASK_VERIFIED = "task_verified"
    PREPARATION_FAILED = "preparation_failed"
    MAX_STEPS_REACHED = "max_steps_reached"
    MAX_DURATION_REACHED = "max_duration_reached"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    COST_BUDGET_EXHAUSTED = "cost_budget_exhausted"
    MODEL_FAILED = "model_failed"
    CONSECUTIVE_FAILURE_LIMIT = "consecutive_failure_limit"
    EVALUATION_FAILED = "evaluation_failed"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    APPROVAL_REJECTED = "approval_rejected"
    CANCEL_REQUESTED = "cancel_requested"
    INTERNAL_ERROR = "internal_error"


class RuntimeConfig(BaseModel):
    max_steps: int = Field(default=50, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    tool_output_limits: ToolOutputLimits = Field(default_factory=ToolOutputLimits)


class RunError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class RunUsage(BaseModel):
    model_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_input_priced_separately: bool = False
    input_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    cached_input_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    output_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    active_duration_ms: int = Field(default=0, ge=0)


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    status: RunStatus
    reason: TerminationReason
    output: str | None = None
    error: RunError | None = None
    usage: RunUsage
    started_at: datetime | None
    finished_at: datetime

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "RunResult":
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("Run result requires a terminal status")
        if self.status is RunStatus.SUCCEEDED and not self.output:
            raise ValueError("Succeeded result requires output")
        return self

    @classmethod
    def from_state(cls, state: "RunState") -> "RunResult":
        validate_state(state)
        if state.status not in TERMINAL_STATUSES:
            raise ValueError("Cannot create result from non-terminal state")
        if state.termination_reason is None or state.finished_at is None:
            raise ValueError("Terminal state is incomplete")

        return cls(
            run_id=state.run_id,
            status=state.status,
            reason=state.termination_reason,
            output=state.final_output,
            error=state.error.model_copy(deep=True) if state.error else None,
            usage=state.usage.model_copy(deep=True),
            started_at=state.started_at,
            finished_at=state.finished_at,
        )


class RunState(BaseModel):
    run_id: UUID = Field(frozen=True)
    task: str = Field(frozen=True)
    agent_spec: AgentSpec = Field(frozen=True)
    config: RuntimeConfig = Field(frozen=True)

    status: RunStatus = RunStatus.CREATED
    cursor: RunCursor | None = None
    version: int = Field(default=0, ge=0)

    messages: list[Message] = Field(default_factory=list)

    steps_started: int = Field(default=0, ge=0)
    consecutive_failed_steps: int = Field(default=0, ge=0)
    usage: RunUsage = Field(default_factory=RunUsage)

    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    completed_tool_call_ids: set[str] = Field(default_factory=set)

    pending_approval_id: str | None = None
    checkpoint_id: str | None = None
    recovery_attempts: int = Field(default=0, ge=0)

    candidate_output: str | None = None
    final_output: str | None = None
    termination_reason: TerminationReason | None = None
    error: RunError | None = None

    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "RunState":
        validate_state(self)
        return self


class InvalidRunTransition(ValueError):
    pass


def transition(
    state: RunState,
    target: RunStatus,
    *,
    cursor: RunCursor | None = None,
    reason: TerminationReason | None = None,
    error: RunError | None = None,
) -> None:
    if target not in ALLOWED_TRANSITIONS[state.status]:
        raise InvalidRunTransition(
            f"Cannot transition run {state.run_id} from {state.status} to {target}"
        )

    is_terminal = target in TERMINAL_STATUSES
    if is_terminal and reason is None:
        raise ValueError("Terminal transition requires a termination reason")
    if not is_terminal and (reason is not None or error is not None):
        raise ValueError("Non-terminal transition cannot include termination details")

    candidate = state.model_copy()
    now = datetime.now(UTC)
    candidate.status = target
    candidate.cursor = cursor
    candidate.updated_at = now
    candidate.version += 1

    if candidate.started_at is None and target is RunStatus.PREPARING:
        candidate.started_at = now

    if is_terminal:
        candidate.termination_reason = reason
        candidate.error = error
        candidate.finished_at = now

    validate_state(candidate)
    state.status = candidate.status
    state.cursor = candidate.cursor
    state.version = candidate.version
    state.started_at = candidate.started_at
    state.updated_at = candidate.updated_at
    state.finished_at = candidate.finished_at
    state.termination_reason = candidate.termination_reason
    state.error = candidate.error


def validate_state(state: RunState) -> None:
    is_terminal = state.status in TERMINAL_STATUSES

    if state.status is RunStatus.RUNNING and state.cursor is None:
        raise ValueError("Running state requires a cursor")
    if state.status is not RunStatus.RUNNING and state.cursor is not None:
        raise ValueError("Only running state may have a cursor")
    if state.cursor is RunCursor.EXECUTE_TOOL_CALLS and not state.pending_tool_calls:
        raise ValueError("Tool execution cursor requires pending tool calls")
    if state.status is RunStatus.WAITING_APPROVAL:
        if state.pending_approval_id is None:
            raise ValueError("Waiting approval state requires a pending approval")
    elif state.pending_approval_id is not None:
        raise ValueError("Pending approval requires waiting approval state")
    if state.status is RunStatus.EVALUATING and not state.candidate_output:
        raise ValueError("Evaluating state requires candidate output")
    if is_terminal:
        if state.termination_reason is None or state.finished_at is None:
            raise ValueError("Terminal state requires termination details")
    elif state.termination_reason is not None or state.finished_at is not None:
        raise ValueError("Non-terminal state cannot have termination details")
    if state.status is RunStatus.SUCCEEDED and not state.final_output:
        raise ValueError("Succeeded state requires final output")
