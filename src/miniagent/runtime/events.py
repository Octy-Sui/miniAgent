# File   : events.py
# Created: 2026-09-11 18:44:27
# Author : octy Sui

from collections.abc import Awaitable
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from miniagent.models.artifact import ArtifactRef
from miniagent.runtime.tracing import SpanId


class EventSequenceConflictError(RuntimeError):
    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected event sequence {expected}, found {actual}")


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    STATE_TRANSITIONED = "state_transitioned"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    TOOL_CALLED = "tool_called"
    TOOL_COMPLETED = "tool_completed"
    FILE_CHANGED = "file_changed"
    TEST_COMPLETED = "test_completed"
    RETRY_SCHEDULED = "retry_scheduled"
    STALL_DETECTED = "stall_detected"
    CHECKPOINT_CREATED = "checkpoint_created"
    ROLLBACK_COMPLETED = "rollback_completed"
    CONTEXT_COMPACTED = "context_compacted"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    APPROVAL_INVALIDATED = "approval_invalidated"
    SNAPSHOT_CREATED = "snapshot_created"
    EVALUATION_COMPLETED = "evaluation_completed"
    RUN_FINISHED = "run_finished"


class NewRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    run_id: UUID
    type: RunEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    step_number: int | None = Field(default=None, ge=1)
    span_id: SpanId | None = None
    parent_span_id: SpanId | None = None

    @model_validator(mode="after")
    def validate_trace_scope(self) -> "NewRunEvent":
        if self.parent_span_id is not None and self.span_id is None:
            raise ValueError("Parent span requires a current span")
        if self.parent_span_id == self.span_id and self.span_id is not None:
            raise ValueError("Event span cannot be its own parent")
        if self.type in {
            RunEventType.STEP_STARTED,
            RunEventType.STEP_COMPLETED,
        }:
            if self.step_number is None:
                raise ValueError("Step event requires a step number")
            if self.span_id is None:
                raise ValueError("Step event requires a span")
        return self


class RunEvent(NewRunEvent):
    event_id: UUID
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime


class EventStore(Protocol):
    def append(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> Awaitable[RunEvent]: ...

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[RunEvent]: ...

    async def latest_sequence(self, run_id: UUID) -> int: ...
