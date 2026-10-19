from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from miniagent.metering.usage import RunUsage
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
    from miniagent.artifacts.contracts import ArtifactRecord
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


@dataclass(frozen=True)
class ScopeFacts:
    span_id: str
    events: tuple[StoredEvent, ...]
    start_count: int
    end_count: int
    loaded: bool


@dataclass(frozen=True)
class ScopeRead:
    scopes: dict[str, ScopeFacts]
    complete: bool
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class CompactionRead:
    events: tuple[StoredEvent, ...]
    snapshots: dict[int, StoredSnapshot]
    source_events: dict[UUID, StoredEvent]
    unloaded_versions: tuple[int, ...]
    unloaded_event_ids: tuple[UUID, ...]
    issues: tuple[ReadIssue, ...]


def _missing_state_fields(raw: object) -> tuple[ReadIssue, ...]:

    paths = [
        *(("usage", key) for key in RunUsage.model_fields),
        ("execution", "steps_started"),
        ("execution", "consecutive_failed_steps"),
        ("definition", "config", "max_steps"),
        ("definition", "config", "max_consecutive_failures"),
        ("definition", "config", "context_window_tokens"),
        ("definition", "agent_spec", "generation", "max_output_tokens"),
        ("context", "current_version"),
    ]
    issues = []
    conversation = raw.get("conversation") if isinstance(raw, dict) else None
    messages = conversation.get("messages") if isinstance(conversation, dict) else None
    if isinstance(messages, list):
        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "assistant":
                for key in ("content", "tool_calls"):
                    if key not in message:
                        issues.append(
                            ReadIssue(
                                "field_not_recorded",
                                "历史消息字段未记录",
                                field_path=("conversation", "messages", index, key),
                            )
                        )

    for path in paths:
        value = raw
        for key in path:
            if not isinstance(value, dict) or key not in value:
                issues.append(
                    ReadIssue("field_not_recorded", "历史字段未记录", field_path=path)
                )
                break
            value = value[key]
    return tuple(issues)


@dataclass(frozen=True)
class ManifestItem:
    artifact_id: UUID
    record: ArtifactRecord | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class ManifestPage:
    items: tuple[ManifestItem, ...]
    next_id: UUID | None
    read_at: datetime
    complete: bool
    issues: tuple[ReadIssue, ...] = ()


@dataclass(frozen=True)
class PatchDiscovery:
    items: tuple[ManifestItem, ...]
    complete: bool
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class VerifiedContent:
    record: ArtifactRecord
    content: bytes | None
    read_at: datetime
    issues: tuple[ReadIssue, ...]
