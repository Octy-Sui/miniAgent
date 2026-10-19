from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from miniagent.dashboard.reads import ReadIssue


@dataclass(frozen=True)
class EventView:
    event_id: UUID
    sequence: int
    type: str
    step_number: int | None
    summary: str
    payload: JsonValue | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class ToolView:
    run_id: UUID
    step_number: int
    tool_call_id: str
    status: str | None
    completeness: str | None
    exit_code: int | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class SpanView:
    span_id: str
    category: str
    state: str
    duration_ms: int | None
    start_event_id: UUID | None
    end_event_id: UUID | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class SourceTarget:
    run_id: UUID
    event_id: UUID | None = None
    snapshot_version: int | None = None
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class FieldView:
    key: str
    label: str
    value: str
    source_label: str
    source: SourceTarget | None = None


@dataclass(frozen=True)
class RunView:
    run_id: UUID
    status: str
    terminal: bool
    identity: tuple[FieldView, ...]
    result: tuple[FieldView, ...]
    usage: tuple[FieldView, ...]
    budgets: tuple[FieldView, ...]
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class EvidenceView:
    claim: str
    state: Literal["verified", "inconsistent", "not_loaded"]
    source_label: str
    source: SourceTarget | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class CompactionAttemptView:
    attempt: int
    cost_usd: Decimal | None
    fields: tuple[FieldView, ...]
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class CompactionGroupView:
    first_requested_sequence: int | None
    attempts: tuple[CompactionAttemptView, ...]
    total_cost_usd: Decimal | None
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class MemoryView:
    version: int | None
    fields: tuple[FieldView, ...]
    evidence: tuple[EvidenceView, ...]
    groups: tuple[CompactionGroupView, ...]
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class PreviewView:
    artifact_id: UUID
    fields: tuple[FieldView, ...]
    text: str | None
    offset: int
    next_offset: int | None
    inline_continuation: bool
    read_at: datetime
    issues: tuple[ReadIssue, ...]


@dataclass(frozen=True)
class EvaluationAttemptView:
    attempt_id: UUID | None
    outcome: Literal[
        "recorded_pass", "conflict", "incomplete", "unsupported", "recorded_failure"
    ]
    fields: tuple[FieldView, ...]
    artifact_ids: tuple[UUID, ...]
    issues: tuple[ReadIssue, ...]
