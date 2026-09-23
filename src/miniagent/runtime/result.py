from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from miniagent.metering.usage import RunUsage

if TYPE_CHECKING:
    from miniagent.runtime.state import RunState, RunStatus


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
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_UNAVAILABLE = "approval_unavailable"
    CANCEL_REQUESTED = "cancel_requested"
    INTERNAL_ERROR = "internal_error"


class RunError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    status: "RunStatus"
    reason: TerminationReason
    output: str | None = None
    error: RunError | None = None
    usage: RunUsage
    started_at: datetime | None
    finished_at: datetime

    @model_validator(mode="after")
    def validate_terminal_result(self) -> "RunResult":
        from miniagent.runtime.state import TERMINAL_STATUSES, RunStatus

        if self.status not in TERMINAL_STATUSES:
            raise ValueError("Run result requires a terminal status")
        if self.status is RunStatus.SUCCEEDED and not self.output:
            raise ValueError("Succeeded result requires output")
        return self

    @classmethod
    def from_state(cls, state: "RunState") -> "RunResult":
        from miniagent.runtime.state import TERMINAL_STATUSES, validate_state

        validate_state(state)
        lifecycle = state.lifecycle
        if lifecycle.status not in TERMINAL_STATUSES:
            raise ValueError("Cannot create result from non-terminal state")
        if lifecycle.termination_reason is None or lifecycle.finished_at is None:
            raise ValueError("Terminal state is incomplete")
        return cls(
            run_id=state.identity.run_id,
            status=lifecycle.status,
            reason=lifecycle.termination_reason,
            output=lifecycle.final_output,
            error=(lifecycle.error.model_copy(deep=True) if lifecycle.error else None),
            usage=state.usage.model_copy(deep=True),
            started_at=lifecycle.started_at,
            finished_at=lifecycle.finished_at,
        )
