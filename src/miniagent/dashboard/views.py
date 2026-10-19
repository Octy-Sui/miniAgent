from dataclasses import dataclass
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
