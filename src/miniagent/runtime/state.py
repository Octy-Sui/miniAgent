# File   : state.py
# Created: 2026-09-10 16:39:36
# Author : octy Sui

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field

from miniagent.models.model import Message


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TerminationReason(StrEnum):
    MODEL_COMPLETED = "model_completed"
    MAX_STEPS_REACHED = "max_steps_reached"
    MODEL_FAILED = "model_failed"
    CONSECUTIVE_FAILURE_LIMIT = "consecutive_failure_limit"
    CANCEL_REQUESTED = "cancel_requested"


class RuntimeConfig(BaseModel):
    max_steps: int = Field(default=50, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)


class RunError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class RunResult(BaseModel):
    status: RunStatus
    reason: TerminationReason
    output: str | None = None
    steps: int
    consecutive_failures: int
    messages: list[Message]
    error: RunError | None = None


@dataclass
class RunState:
    messages: list[Message]
    steps_started: int = 0
    consecutive_failures: int = 0
