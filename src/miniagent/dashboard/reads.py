from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from miniagent.runtime.state import RunStatus


@dataclass(frozen=True)
class ReadIssue:
    code: str
    message: str
    subject_id: UUID | None = None
    field_path: tuple[str | int, ...] = ()


class ReadFailure(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


@dataclass
class ReadBudget:
    deadline: float
    remaining_bytes: int = 67108864
    monotonic: Callable[[], float] = time.monotonic

    def check(self) -> None:
        if self.monotonic() >= self.deadline:
            raise ReadFailure(503, "read_deadline")

    def reserve(self, size: int) -> bool:
        self.check()
        if size < 0:
            raise ValueError("negative read reservation")
        if size > self.remaining_bytes:
            return False
        self.remaining_bytes -= size
        return True


if TYPE_CHECKING:
    from miniagent.dashboard.cursors import PageBoundary, RunPosition
    from miniagent.models.messages import Message
    from miniagent.runtime.events import RunEvent
    from miniagent.runtime.snapshots import RunSnapshot
    from miniagent.runtime.store_contracts import RunRecord


@dataclass(frozen=True)
class RunEnvelope:
    run_id: UUID
    schema_version: int
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    last_event_sequence: int
    latest_snapshot_version: int


@dataclass(frozen=True)
class RunListItem:
    envelope: RunEnvelope
    task: str | None
    model: str | None
    workspace_kind: str
    reason: str | None
    active_duration_ms: int | None
    cost_usd: Decimal | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class RunListRead:
    items: tuple[RunListItem, ...]
    total: int
    counts: dict[str, int]
    next_position: RunPosition | None
    read_at: datetime


@dataclass(frozen=True)
class StoredRun:
    envelope: RunEnvelope
    record: RunRecord | None
    workspace_kind: Literal["local", "docker", "unknown"]
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class StoredEvent:
    event_id: UUID
    run_id: UUID
    sequence: int
    type: str
    occurred_at: datetime
    step_number: int | None
    span_id: str | None
    parent_span_id: str | None
    event: RunEvent | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class EventBatch:
    items: tuple[StoredEvent, ...]
    next_sequence: int | None


@dataclass(frozen=True)
class StoredSnapshot:
    snapshot_id: UUID
    run_id: UUID
    version: int
    event_sequence: int
    snapshot: RunSnapshot | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class RunPageRead:
    run: StoredRun
    boundary: PageBoundary
    events: EventBatch
    evaluation_event: StoredEvent | None = None


@dataclass(frozen=True)
class SnapshotSlice:
    snapshot: StoredSnapshot
    start: int
    end: int
    message_count: int | None
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class ToolFacts:
    events: tuple[StoredEvent, ...]
    counts: dict[tuple[int, str, str], int]
    issues: tuple[ReadIssue, ...]
