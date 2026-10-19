import sqlite3
from collections import deque
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
    ScopeFacts,
    ScopeRead,
    StoredEvent,
    ToolFacts,
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
    conditions, values = _event_conditions(filters)
    rows = connection.execute(
        f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence>? AND sequence<=? AND {conditions} ORDER BY sequence LIMIT ?",
        (
            str(boundary.run_id),
            after_sequence,
            boundary.as_of_sequence,
            *values,
            limit + 1,
        ),
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


def read_tool_facts(
    database: Path,
    boundary: PageBoundary,
    keys: tuple[tuple[int, str], ...],
    *,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> ToolFacts:
    if len(keys) > 200:
        raise ReadFailure(422, "tool_keys_invalid")
    events: list[StoredEvent] = []
    counts: dict[tuple[int, str, str], int] = {}
    issues: list[ReadIssue] = []
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        for step, call_id in dict.fromkeys(keys):
            budget.check()
            predicate = f"run_id=? AND sequence<=? AND step_number=? AND type IN ('tool_start','tool_end','tool_execution_start','tool_execution_end') AND json_extract({_PAYLOAD},'$.tool_call_id')=?"
            values = (str(boundary.run_id), boundary.as_of_sequence, step, call_id)
            for role, count in connection.execute(
                f"SELECT type,count(*) FROM run_events WHERE {predicate} GROUP BY type",
                values,
            ):
                counts[(step, call_id, role)] = count
                rows = connection.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE {predicate} AND type=? ORDER BY sequence LIMIT 2",
                    (*values, role),
                ).fetchall()
                events.extend(_load_event(connection, row, budget) for row in rows)
    if any(event.event is None for event in events):
        issues.append(ReadIssue("not_loaded", "部分工具事实未完成读取"))
    return ToolFacts(tuple(events), counts, tuple(issues))


_PAYLOAD = "CASE WHEN json_valid(payload_json) AND length(CAST(payload_json AS BLOB))<=1048576 THEN payload_json ELSE '{}' END"
_TOOL_TYPES = (
    "tool_start",
    "tool_end",
    "tool_execution_start",
    "tool_execution_end",
    "policy_decided",
    "approval_requested",
    "approval_resolved",
)


def _event_conditions(filters: EventFilters) -> tuple[str, list[object]]:
    conditions = []
    values: list[object] = []
    if filters.types:
        conditions.append("type IN (" + ",".join("?" for _ in filters.types) + ")")
        values.extend(filters.types)
    if filters.step is not None:
        conditions.append("step_number=?")
        values.append(filters.step)
    if filters.tool_name is not None:
        conditions.append(
            f"type IN ({','.join('?' for _ in _TOOL_TYPES)}) AND json_extract({_PAYLOAD},'$.tool_name')=?"
        )
        values.extend((*_TOOL_TYPES, filters.tool_name))
    if filters.failures_only:
        conditions.append(f"""(
(type='model_responded' AND json_type({_PAYLOAD},'$.error_code')='text' AND json_extract({_PAYLOAD},'$.error_code')!='') OR
(type IN ('tool_end','tool_execution_end') AND json_extract({_PAYLOAD},'$.status') IN ('error','timeout','denied')) OR
(type='step_completed' AND json_type({_PAYLOAD},'$.failed')='true') OR
(type='evaluation_completed' AND json_type({_PAYLOAD},'$.passed')='false') OR
(type='run_finished' AND json_extract({_PAYLOAD},'$.status')='failed') OR
(type IN ('compaction_failed','approval_invalidated')) OR
(type='approval_resolved' AND json_extract({_PAYLOAD},'$.outcome') IN ('rejected','expired','invalidated'))
)""")
    return " AND ".join(conditions) or "1", values


def read_scope_facts(
    database: Path,
    boundary: PageBoundary,
    span_ids: tuple[str, ...],
    *,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> ScopeRead:
    starts = (
        "run_started",
        "step_started",
        "model_requested",
        "tool_start",
        "tool_execution_start",
        "evaluation_start",
        "approval_requested",
    )
    ends = (
        "run_finished",
        "step_completed",
        "model_responded",
        "tool_end",
        "tool_execution_end",
        "evaluation_completed",
        "approval_resolved",
    )
    roles = (*starts, *ends)
    scopes: dict[str, ScopeFacts] = {}
    pending = deque((span_id, 1) for span_id in dict.fromkeys(span_ids))
    queued = set(span_ids)
    complete = True
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        while pending:
            if len(scopes) >= LIMITS.scopes or budget.monotonic() >= budget.deadline:
                complete = False
                break
            span_id, depth = pending.popleft()
            if span_id in scopes:
                continue
            if depth > LIMITS.parent_depth:
                complete = False
                continue
            predicate = f"run_id=? AND sequence<=? AND span_id=? AND type IN ({','.join('?' for _ in roles)})"
            values = (str(boundary.run_id), boundary.as_of_sequence, span_id, *roles)
            counts = dict(
                connection.execute(
                    f"SELECT type,count(*) FROM run_events WHERE {predicate} GROUP BY type",
                    values,
                )
            )
            loaded = []
            for role in counts:
                rows = connection.execute(
                    f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE {predicate} AND type=? ORDER BY sequence LIMIT 2",
                    (*values, role),
                ).fetchall()
                loaded.extend(_load_event(connection, row, budget) for row in rows)
            scopes[span_id] = ScopeFacts(
                span_id,
                tuple(loaded),
                sum(counts.get(role, 0) for role in starts),
                sum(counts.get(role, 0) for role in ends),
                all(item.event is not None for item in loaded),
            )
            for parent in dict.fromkeys(
                item.parent_span_id
                for item in loaded
                if item.parent_span_id is not None
            ):
                if parent not in queued:
                    queued.add(parent)
                    if (
                        depth >= LIMITS.parent_depth
                        or len(scopes) + len(pending) >= LIMITS.scopes
                    ):
                        complete = False
                    else:
                        pending.append((parent, depth + 1))
    issues = (
        ()
        if complete and all(scope.loaded for scope in scopes.values())
        else (ReadIssue("not_loaded", "部分 scope 未完成加载"),)
    )
    return ScopeRead(scopes, complete, issues)
