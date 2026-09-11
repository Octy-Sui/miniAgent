# File   : events.py
# Created: 2026-09-11 18:44:27
# Author : octy Sui

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from miniagent.models.artifact import ArtifactRef


class EventSequenceConflictError(RuntimeError):
    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected event sequence {expected}, found {actual}")


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    STATE_TRANSITIONED = "state_transitioned"
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
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    SNAPSHOT_CREATED = "snapshot_created"
    EVALUATION_COMPLETED = "evaluation_completed"
    RUN_FINISHED = "run_finished"


class NewRunEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    type: RunEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    parent_event_id: UUID | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    trace_id: str | None = None
    span_id: str | None = None


class RunEvent(NewRunEvent):
    event_id: UUID
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime


class EventStore(Protocol):
    async def append(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> RunEvent: ...

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[RunEvent]: ...

    async def latest_sequence(self, run_id: UUID) -> int: ...
