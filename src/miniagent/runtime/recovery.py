# File   : recovery.py
# Created: 2026-09-11 18:44:37
# Author : octy Sui

from enum import IntEnum, StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.agents import ModelTarget
from miniagent.environments import WorkspaceCheckpoint
from miniagent.runtime.events import RunEvent
from miniagent.runtime.state import RunError, RunState


class RecoveryLevel(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3
    L4 = 4
    L5 = 5


class RecoveryTrigger(StrEnum):
    PROVIDER_ERROR = "provider_error"
    TOOL_ERROR = "tool_error"
    STALL = "stall"
    REGRESSION = "regression"
    BUDGET_PRESSURE = "budget_pressure"
    INTERRUPTED = "interrupted"


class RecoveryAction(StrEnum):
    RETURN_ERROR = "return_error"
    RETRY = "retry"
    REPLAN = "replan"
    ROLLBACK = "rollback"
    SWITCH_MODEL = "switch_model"
    REQUEST_APPROVAL = "request_approval"
    TERMINATE = "terminate"


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: RunState
    trigger: RecoveryTrigger
    error: RunError | None = None
    recent_events: list[RunEvent] = Field(default_factory=list)
    latest_checkpoint: WorkspaceCheckpoint | None = None

    @model_validator(mode="after")
    def validate_run_scope(self) -> "RecoveryRequest":
        if any(event.run_id != self.state.run_id for event in self.recent_events):
            raise ValueError("Recovery events must belong to the current run")
        if (
            self.latest_checkpoint is not None
            and self.latest_checkpoint.run_id != self.state.run_id
        ):
            raise ValueError("Recovery checkpoint must belong to the current run")
        return self


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: RecoveryLevel
    action: RecoveryAction
    reason: str
    evidence_event_ids: list[UUID] = Field(default_factory=list)
    retry_delay_seconds: float | None = Field(default=None, ge=0)
    checkpoint: WorkspaceCheckpoint | None = None
    replacement_model: ModelTarget | None = None

    @model_validator(mode="after")
    def validate_action_parameters(self) -> "RecoveryDecision":
        allowed_actions = {
            RecoveryLevel.L0: {RecoveryAction.RETURN_ERROR},
            RecoveryLevel.L1: {RecoveryAction.RETRY},
            RecoveryLevel.L2: {RecoveryAction.REPLAN},
            RecoveryLevel.L3: {RecoveryAction.ROLLBACK},
            RecoveryLevel.L4: {
                RecoveryAction.SWITCH_MODEL,
                RecoveryAction.REQUEST_APPROVAL,
            },
            RecoveryLevel.L5: {RecoveryAction.TERMINATE},
        }
        if self.action not in allowed_actions[self.level]:
            raise ValueError(
                f"Recovery action {self.action} is invalid for level {self.level}"
            )

        if self.action is RecoveryAction.RETRY:
            if self.retry_delay_seconds is None:
                raise ValueError("Retry decision requires a delay")
        elif self.retry_delay_seconds is not None:
            raise ValueError("Only retry decisions may define a delay")

        if self.action is RecoveryAction.ROLLBACK:
            if self.checkpoint is None:
                raise ValueError("Rollback decision requires a checkpoint")
        elif self.checkpoint is not None:
            raise ValueError("Only rollback decisions may define a checkpoint")

        if self.action is RecoveryAction.SWITCH_MODEL:
            if self.replacement_model is None:
                raise ValueError("Model switch requires a replacement model")
        elif self.replacement_model is not None:
            raise ValueError("Only model switch decisions may define a model")
        return self


class RecoveryController(Protocol):
    async def decide(self, request: RecoveryRequest) -> RecoveryDecision: ...
