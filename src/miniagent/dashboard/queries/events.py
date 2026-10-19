import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.connection import ConnectionFactory, read_transaction
from miniagent.dashboard.reads import (
    EventBatch,
    ReadBudget,
    ReadFailure,
    ReadIssue,
    StoredEvent,
)
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    UnsupportedStorageVersionError,
)
from miniagent.storage.codecs import decode_event

_EVENT_COLUMNS = """event_id,run_id,sequence,type,occurred_at,step_number,span_id,parent_span_id,schema_version,
length(CAST(payload_json AS BLOB)) AS payload_bytes,length(CAST(artifacts_json AS BLOB)) AS artifacts_bytes"""


def _validate_boundary(connection: sqlite3.Connection, boundary: PageBoundary) -> None:
    row = connection.execute(
        "SELECT last_event_sequence,latest_snapshot_version FROM runs WHERE run_id=?",
        (str(boundary.run_id),),
    ).fetchone()
    if row is None:
        raise ReadFailure(404, "run_missing")
    if boundary.as_of_sequence > row[0] or boundary.latest_snapshot_version > row[1]:
        raise ReadFailure(422, "boundary_invalid")


def _load_event(
    connection: sqlite3.Connection, row: sqlite3.Row, budget: ReadBudget
) -> StoredEvent:
    budget.check()
    event = None
    issues: list[ReadIssue] = []
    if max(
        row["payload_bytes"], row["artifacts_bytes"]
    ) > LIMITS.event_bytes or not budget.reserve(
        row["payload_bytes"] + row["artifacts_bytes"]
    ):
        issues.append(ReadIssue("read_limit_exceeded", "事件正文超出读取上限"))
    else:
        body = connection.execute(
            "SELECT * FROM run_events WHERE run_id=? AND event_id=?",
            (row["run_id"], row["event_id"]),
        ).fetchone()
        try:
            parse_json(body["payload_json"], budget=budget)
            parse_json(body["artifacts_json"], budget=budget)
            event = decode_event(body)
        except (ContentError, CorruptStoredDataError, UnsupportedStorageVersionError):
            issues.append(ReadIssue("payload_unsupported", "事件正文无效或版本不支持"))
    return StoredEvent(
        event_id=UUID(row["event_id"]),
        run_id=UUID(row["run_id"]),
        sequence=row["sequence"],
        type=row["type"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        step_number=row["step_number"],
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        event=event,
        issues=tuple(issues),
    )


def _read_events(
    connection: sqlite3.Connection,
    boundary: PageBoundary,
    filters: EventFilters,
    *,
    after_sequence: int,
    limit: int,
    budget: ReadBudget,
) -> EventBatch:
    if not 1 <= limit <= 200 or not 0 <= after_sequence <= boundary.as_of_sequence:
        raise ReadFailure(422, "event_range_invalid")
    if filters != EventFilters():
        raise ReadFailure(422, "filters_not_supported")
    rows = connection.execute(
        f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?",
        (str(boundary.run_id), after_sequence, boundary.as_of_sequence, limit + 1),
    ).fetchall()
    items = tuple(_load_event(connection, row, budget) for row in rows[:limit])
    return EventBatch(items, items[-1].sequence if len(rows) > limit else None)


def read_events(
    database: Path,
    boundary: PageBoundary,
    filters: EventFilters,
    *,
    after_sequence: int = 0,
    limit: int = 100,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> EventBatch:
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        return _read_events(
            connection,
            boundary,
            filters,
            after_sequence=after_sequence,
            limit=limit,
            budget=budget,
        )


def locate_event(
    database: Path,
    boundary: PageBoundary,
    event_id: UUID,
    *,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> EventBatch:
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        target = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND event_id=?",
            (str(boundary.run_id), str(event_id)),
        ).fetchone()
        if target is None:
            raise ReadFailure(404, "event_missing")
        if target["sequence"] > boundary.as_of_sequence:
            raise ReadFailure(422, "boundary_invalid")
        previous = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence<? ORDER BY sequence DESC LIMIT 20",
            (str(boundary.run_id), target["sequence"]),
        ).fetchall()
        following = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT 20",
            (str(boundary.run_id), target["sequence"], boundary.as_of_sequence),
        ).fetchall()
        return EventBatch(
            tuple(
                _load_event(connection, row, budget)
                for row in [*reversed(previous), target, *following]
            ),
            None,
        )
