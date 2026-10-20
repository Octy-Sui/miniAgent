import sqlite3
from collections import deque
from importlib.resources import files
from pathlib import Path
from uuid import UUID

from pydantic import JsonValue, ValidationError

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.connection import (
    ConnectionFactory,
    _validate_boundary,
    read_transaction,
)
from miniagent.dashboard.queries.snapshots import _read_snapshot_record
from miniagent.dashboard.reads import (
    CompactionRead,
    EventBatch,
    ReadBudget,
    ReadFailure,
    ReadIssue,
    ScopeFacts,
    ScopeRead,
    StoredEvent,
    StoredRun,
    StoredSnapshot,
    ToolFacts,
    recorded_time,
)
from miniagent.models.messages import AssistantMessage
from miniagent.runtime.context import CompactionSummary, EventSource
from miniagent.runtime.events import CompactionCompleted
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    UnsupportedStorageVersionError,
)
from miniagent.storage.codecs import decode_event

_EVENT_COLUMNS = """event_id,run_id,sequence,type,occurred_at,step_number,span_id,parent_span_id,schema_version,
length(CAST(payload_json AS BLOB)) AS payload_bytes,length(CAST(artifacts_json AS BLOB)) AS artifacts_bytes"""


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
            raw = parse_json(body["payload_json"], budget=budget)
            parse_json(body["artifacts_json"], budget=budget)
            event = decode_event(body)
            if _json_changed(
                raw, event.payload.model_dump(mode="json", exclude_unset=True)
            ):
                event = None
                issues.append(
                    ReadIssue(
                        "payload_unsupported", "事件源字段发生类型转换，保留登记信息"
                    )
                )
            del raw
        except (ContentError, CorruptStoredDataError, UnsupportedStorageVersionError):
            issues.append(ReadIssue("payload_unsupported", "事件正文无效或版本不支持"))
    return StoredEvent(
        event_id=UUID(row["event_id"]),
        run_id=UUID(row["run_id"]),
        sequence=row["sequence"],
        type=row["type"],
        occurred_at=recorded_time(row["occurred_at"], issues, "occurred_at"),
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
    limit = min(limit, 16)
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


_PAYLOAD = "CASE WHEN json_valid(payload_json) THEN payload_json ELSE '{}' END"
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


def read_compaction_sources(
    database: Path,
    boundary: PageBoundary,
    *,
    run: StoredRun | None = None,
    version: int | None = None,
    visible_events: tuple[StoredEvent, ...] = (),
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> CompactionRead:
    if run is None and version is None:
        raise ReadFailure(422, "compaction_selection_missing")
    if version is not None and version < 1:
        raise ReadFailure(422, "compaction_version_invalid")
    selected = (
        version
        if version is not None
        else (
            run.record.state.context.current_version
            if run is not None and run.record is not None
            else None
        )
    )
    events = {
        item.event_id: item
        for item in visible_events
        if item.type.startswith("compaction_")
        and item.run_id == boundary.run_id
        and item.sequence <= boundary.as_of_sequence
    }
    snapshots: dict[int, StoredSnapshot] = {}
    source_events: dict[UUID, StoredEvent] = {}
    unloaded = []
    unloaded_events = []
    issues = []
    chain = []
    seen = set()
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        while selected is not None:
            budget.check()
            if len(seen) >= LIMITS.compaction_versions:
                unloaded.append(selected)
                issues.append(ReadIssue("not_loaded", "压缩版本链超过本次读取上限"))
                break
            if selected in seen:
                issues.append(
                    ReadIssue("compaction_chain_inconsistent", "压缩版本链成环")
                )
                break
            seen.add(selected)
            rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence<=? AND type='compaction_completed' AND json_extract({_PAYLOAD},'$.current_version')=? ORDER BY sequence LIMIT 2",
                (str(boundary.run_id), boundary.as_of_sequence, selected),
            ).fetchall()
            if not rows:
                unresolved = connection.execute(
                    "SELECT 1 FROM run_events WHERE run_id=? AND sequence<=? AND type='compaction_completed' AND (NOT json_valid(payload_json) OR length(CAST(payload_json AS BLOB))>1048576) LIMIT 1",
                    (str(boundary.run_id), boundary.as_of_sequence),
                ).fetchone()
                if unresolved:
                    unloaded.append(selected)
                    issues.append(ReadIssue("not_loaded", "压缩记录尚未完整读取"))
                else:
                    issues.append(
                        ReadIssue(
                            "source_missing",
                            "压缩版本记录不存在",
                            field_path=("compactions", selected),
                        )
                    )
                break
            loaded = [_load_event(connection, row, budget) for row in rows]
            events.update((item.event_id, item) for item in loaded)
            if len(loaded) != 1:
                issues.append(ReadIssue("compaction_duplicate", "压缩版本重复记录"))
                break
            item = loaded[0]
            if item.event is None or not isinstance(
                item.event.payload, CompactionCompleted
            ):
                unloaded.append(selected)
                break
            chain.append(item.event.payload)
            selected = item.event.payload.previous_version
        wanted = sorted(
            {
                number
                for payload in chain
                for number in (
                    payload.source_snapshot_version,
                    payload.new_snapshot_version,
                )
                if number is not None
            }
        )
        allowed = [
            number
            for number in wanted
            if 1 <= number <= boundary.latest_snapshot_version
        ]
        for number in set(wanted) - set(allowed):
            issues.append(
                ReadIssue(
                    "source_outside_boundary",
                    "Snapshot 引用超出页面边界",
                    field_path=("snapshots", number),
                )
            )
        if allowed:
            rows = connection.execute(
                f"SELECT snapshot_id,run_id,version,event_sequence,length(CAST(snapshot_json AS BLOB)) AS size FROM run_snapshots WHERE run_id=? AND version IN ({','.join('?' for _ in allowed)})",
                (str(boundary.run_id), *allowed),
            ).fetchall()
            present = set()
            for row in rows:
                present.add(row["version"])
                if row["event_sequence"] > boundary.as_of_sequence:
                    issues.append(
                        ReadIssue(
                            "source_outside_boundary",
                            "Snapshot 引用超出页面边界",
                            field_path=("snapshots", row["version"]),
                        )
                    )
                    continue
                snapshots[row["version"]] = _read_snapshot_record(
                    connection, boundary, row["version"], budget, envelope=row
                )
            for number in set(allowed) - present:
                issues.append(
                    ReadIssue(
                        "source_missing",
                        "来源 Snapshot 不存在",
                        field_path=("snapshots", number),
                    )
                )
        endings = [
            item
            for item in events.values()
            if item.type in ("compaction_completed", "compaction_failed")
        ]
        for ending in endings:
            start = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence<=? AND type='compaction_requested' AND json_type({_PAYLOAD},'$.attempt')='integer' AND json_extract({_PAYLOAD},'$.attempt')=1 ORDER BY sequence DESC LIMIT 1",
                (str(boundary.run_id), ending.sequence),
            ).fetchone()
            if start is None:
                continue
            rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND sequence>=? AND sequence<=? AND type IN ('compaction_requested','compaction_attempt_recorded','compaction_completed','compaction_failed') ORDER BY sequence LIMIT 201",
                (str(boundary.run_id), start["sequence"], ending.sequence),
            ).fetchall()
            if len(rows) > 200:
                issues.append(
                    ReadIssue(
                        "not_loaded",
                        "压缩事件组超过读取上限",
                        field_path=("compaction_group", start["sequence"]),
                    )
                )
            for row in rows[:200]:
                event_id = UUID(row["event_id"])
                if event_id not in events:
                    events[event_id] = _load_event(connection, row, budget)
        states = []
        if version is None and run is not None and run.record is not None:
            states.append(run.record.state)
        elif chain:
            carrier = snapshots.get(chain[0].new_snapshot_version or 0)
            if carrier is not None and carrier.snapshot is not None:
                states.append(carrier.snapshot.state)
        event_ids = set()
        prefix = (
            files("miniagent.runtime")
            .joinpath("templates/compaction_v1.j2")
            .read_text()
            .split("{{ summary_json }}", 1)[0]
        )
        for state in states:
            messages = state.conversation.messages
            if (
                len(messages) < 3
                or not isinstance(messages[2], AssistantMessage)
                or messages[2].tool_calls
                or not (messages[2].content or "").startswith(prefix)
            ):
                continue
            try:
                summary = CompactionSummary.model_validate(
                    parse_json(
                        (messages[2].content or "")[len(prefix) :], budget=budget
                    )
                )
            except (ContentError, ValidationError):
                continue
            event_ids.update(
                proof.source.event_id
                for proof in summary.verified_evidence
                if isinstance(proof.source, EventSource)
                and proof.source.run_id == boundary.run_id
            )
        identifiers = tuple(sorted(event_ids))
        for offset in range(0, len(identifiers), 200):
            batch = identifiers[offset : offset + 200]
            rows = connection.execute(
                f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND event_id IN ({','.join('?' for _ in batch)})",
                (str(boundary.run_id), *(str(item) for item in batch)),
            ).fetchall()
            present = set()
            for row in rows:
                event_id = UUID(row["event_id"])
                present.add(event_id)
                if row["sequence"] > boundary.as_of_sequence:
                    issues.append(
                        ReadIssue(
                            "source_outside_boundary",
                            "来源 Event 超出页面边界",
                            subject_id=event_id,
                        )
                    )
                    continue
                loaded = _load_event(connection, row, budget)
                source_events[event_id] = loaded
                if loaded.event is None:
                    unloaded_events.append(event_id)
            for event_id in set(batch) - present:
                issues.append(
                    ReadIssue(
                        "source_missing", "来源 Event 不存在", subject_id=event_id
                    )
                )
    return CompactionRead(
        tuple(sorted(events.values(), key=lambda item: item.sequence)),
        snapshots,
        source_events,
        tuple(unloaded),
        tuple(unloaded_events),
        tuple(issues),
    )


def _json_changed(original: JsonValue, decoded: JsonValue) -> bool:
    if isinstance(original, dict) and isinstance(decoded, dict):
        return any(
            key not in decoded or _json_changed(value, decoded[key])
            for key, value in original.items()
        )
    if isinstance(original, list) and isinstance(decoded, list):
        return len(original) != len(decoded) or any(
            _json_changed(a, b) for a, b in zip(original, decoded, strict=False)
        )
    return type(original) is not type(decoded) or original != decoded
