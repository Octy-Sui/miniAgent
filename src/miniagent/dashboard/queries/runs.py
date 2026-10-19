import sqlite3
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary, RunPosition, utc_now
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.parameters import EventFilters, RunFilters
from miniagent.dashboard.queries.connection import ConnectionFactory, read_transaction
from miniagent.dashboard.queries.events import (
    _EVENT_COLUMNS,
    _load_event,
    _read_events,
)
from miniagent.dashboard.reads import (
    ReadBudget,
    ReadFailure,
    ReadIssue,
    RunEnvelope,
    RunListItem,
    RunListRead,
    RunPageRead,
    StoredRun,
)
from miniagent.metering.usage import RunUsage
from miniagent.runtime.state import RunStatus
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    UnsupportedStorageVersionError,
)
from miniagent.storage.codecs import (
    decode_recovery_context,
    decode_run,
    encode_datetime,
)

_STATE_OK = (
    f"json_valid(state_json) AND length(CAST(state_json AS BLOB))<={20 * 1024 * 1024}"
)
_STATE_DOC = f"CASE WHEN {_STATE_OK} THEN state_json ELSE '{{}}' END"
_RECOVERY_DOC = "CASE WHEN json_valid(recovery_context_json) AND length(CAST(recovery_context_json AS BLOB))<=1048576 THEN recovery_context_json ELSE '{}' END"
_PATHS = {
    "task": "$.identity.task",
    "model": "$.definition.agent_spec.model.model",
    "reason": "$.lifecycle.termination_reason",
    "active_duration_ms": "$.usage.active_duration_ms",
    "cost_usd": "$.usage.cost_usd",
    "identity_id": "$.identity.run_id",
    "identity_created": "$.identity.created_at",
    "lifecycle_status": "$.lifecycle.status",
    "lifecycle_updated": "$.lifecycle.updated_at",
    "lifecycle_finished": "$.lifecycle.finished_at",
}
_EXTRACTED = ",".join(
    f"json_extract({_STATE_DOC},'{path}') AS {name},json_type({_STATE_DOC},'{path}') AS {name}_type"
    for name, path in _PATHS.items()
)
_PREFIX = (
    f"""WITH extracted AS (
SELECT run_id,schema_version,status,created_at,updated_at,finished_at,
last_event_sequence,latest_snapshot_version,
length(CAST(state_json AS BLOB)) AS state_bytes,
length(CAST(recovery_context_json AS BLOB)) AS recovery_bytes,
{_EXTRACTED},
json_extract({_RECOVERY_DOC},'$.workspace_spec.kind') AS recovery_kind,
json_extract({_RECOVERY_DOC},'$.workspace_spec.run_id') AS recovery_run,
json_extract({_RECOVERY_DOC},'$.schema_version') AS recovery_version
FROM runs), base AS (
SELECT run_id,schema_version,status,created_at,updated_at,finished_at,
last_event_sequence,latest_snapshot_version,state_bytes,recovery_bytes,
CASE WHEN schema_version=3 AND identity_id=run_id AND lifecycle_status=status
AND dashboard_time(identity_created)=created_at AND dashboard_time(lifecycle_updated)=updated_at
AND dashboard_time(lifecycle_finished) IS finished_at THEN 1 ELSE 0 END AS identity_ok,
CASE WHEN recovery_version=2 AND recovery_run=run_id AND recovery_kind IN ('local','docker') THEN recovery_kind ELSE 'unknown' END AS workspace_kind,
"""
    + ",".join(
        f"CASE WHEN length(CAST({name} AS BLOB))<={131072 if name == 'task' else 4096} THEN {name} END AS {name},"
        f"length(CAST({name} AS BLOB)) AS {name}_bytes,{name}_type"
        for name in ("task", "model", "reason", "active_duration_ms", "cost_usd")
    )
    + " FROM extracted) "
)


def _canonical_time(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        return encode_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


def _envelope(row: sqlite3.Row) -> RunEnvelope:
    return RunEnvelope(
        run_id=UUID(row["run_id"]),
        schema_version=row["schema_version"],
        status=RunStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"])
        if row["finished_at"]
        else None,
        last_event_sequence=row["last_event_sequence"],
        latest_snapshot_version=row["latest_snapshot_version"],
    )


def list_runs(
    database: Path,
    filters: RunFilters,
    *,
    page_size: int = 25,
    after: RunPosition | None = None,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> RunListRead:
    if page_size not in (10, 25, 50, 100):
        raise ReadFailure(422, "page_size_invalid")
    conditions: list[str] = []
    values: list[object] = []
    if filters.statuses:
        conditions.append("status IN (" + ",".join("?" for _ in filters.statuses) + ")")
        values.extend(filters.statuses)
    for column, value in (
        ("model", filters.model),
        ("workspace_kind", filters.workspace),
    ):
        if value is not None:
            conditions.append(
                f"{column}=?" + (" AND identity_ok=1" if column == "model" else "")
            )
            values.append(value)
    if filters.run_id is not None:
        conditions.append("run_id=?")
        values.append(str(filters.run_id))
    for operator, value in ((">=", filters.created_from), ("<", filters.created_to)):
        if value is not None:
            conditions.append(f"created_at{operator}?")
            values.append(encode_datetime(value))
    if filters.task is not None:
        conditions.append(
            "identity_ok=1 AND task_type='text' AND task LIKE ? ESCAPE '\\'"
        )
        values.append(
            "%"
            + filters.task.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%"
        )
    where = " AND ".join(conditions) or "1"
    with read_transaction(database, budget, connect=connect) as connection:
        connection.create_function(
            "dashboard_time", 1, _canonical_time, deterministic=True
        )
        counts = dict(
            connection.execute(
                _PREFIX
                + "SELECT status,count(*) FROM base WHERE "
                + where
                + " GROUP BY status",
                values,
            )
        )
        if after is not None:
            where += " AND (created_at,run_id)<(?,?)"
            values.extend((encode_datetime(after.created_at), str(after.run_id)))
        rows = connection.execute(
            _PREFIX
            + "SELECT * FROM base WHERE "
            + where
            + " ORDER BY created_at DESC,run_id DESC LIMIT ?",
            [*values, page_size + 1],
        ).fetchall()
    items = tuple(_list_item(row, budget) for row in rows[:page_size])
    position = (
        RunPosition(
            created_at=items[-1].envelope.created_at, run_id=items[-1].envelope.run_id
        )
        if len(rows) > page_size
        else None
    )
    return RunListRead(items, sum(counts.values()), counts, position, datetime.now(UTC))


def _list_item(row: sqlite3.Row, budget: ReadBudget) -> RunListItem:
    budget.check()
    issues: list[ReadIssue] = []
    if row["schema_version"] != 3:
        issues.append(ReadIssue("schema_unsupported", "记录版本不支持"))
    if (
        row["state_bytes"] > LIMITS.state_bytes
        or (row["recovery_bytes"] or 0) > LIMITS.recovery_bytes
    ):
        issues.append(ReadIssue("read_limit_exceeded", "正文超出读取上限"))
    if not row["identity_ok"] and row["state_bytes"] <= LIMITS.state_bytes:
        issues.append(ReadIssue("run_inconsistent", "状态内容与登记身份不一致"))
    fields: dict[str, object] = {}
    for name in ("task", "model", "reason", "active_duration_ms", "cost_usd"):
        value = row[name]
        if (row[name + "_bytes"] or 0) > (
            LIMITS.task_bytes if name == "task" else LIMITS.scalar_bytes
        ):
            issues.append(
                ReadIssue("read_limit_exceeded", "字段超出读取上限", field_path=(name,))
            )
        if not row["identity_ok"] or not budget.reserve(row[name + "_bytes"] or 0):
            value = None
        fields[name] = value
    cost = None
    if row["cost_usd_type"] == "text" and fields["cost_usd"] is not None:
        try:
            parsed_cost = Decimal(str(fields["cost_usd"]))
            if parsed_cost.is_finite() and parsed_cost >= 0:
                cost = parsed_cost
            else:
                issues.append(ReadIssue("usage_invalid", "费用格式无效"))
        except InvalidOperation:
            issues.append(ReadIssue("usage_invalid", "费用格式无效"))
    duration = fields["active_duration_ms"]
    return RunListItem(
        envelope=_envelope(row),
        task=str(fields["task"])
        if fields["task"] is not None and row["task_type"] == "text"
        else None,
        model=str(fields["model"])
        if fields["model"] is not None and row["model_type"] == "text"
        else None,
        workspace_kind=row["workspace_kind"],
        reason=str(fields["reason"])
        if fields["reason"] is not None and row["reason_type"] == "text"
        else None,
        active_duration_ms=duration
        if type(duration) is int and duration >= 0
        else None,
        cost_usd=cost,
        issues=tuple(issues),
    )


def read_run_page(
    database: Path,
    run_id: UUID,
    filters: EventFilters,
    *,
    limit: int = 100,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> RunPageRead:

    with read_transaction(database, budget, connect=connect) as connection:
        row = connection.execute(
            "SELECT run_id,schema_version,status,created_at,updated_at,finished_at,last_event_sequence,latest_snapshot_version,length(CAST(state_json AS BLOB)) AS state_bytes,length(CAST(recovery_context_json AS BLOB)) AS recovery_bytes FROM runs WHERE run_id=?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise ReadFailure(404, "run_missing")
        run = _load_run(connection, row, budget)
        boundary = PageBoundary(
            run_id=run_id,
            as_of_sequence=row["last_event_sequence"],
            latest_snapshot_version=row["latest_snapshot_version"],
            run_status=row["status"],
            read_at=utc_now(),
        )
        events = _read_events(
            connection, boundary, filters, after_sequence=0, limit=limit, budget=budget
        )
        evaluation_row = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM run_events WHERE run_id=? AND type='evaluation_completed' AND sequence<=? ORDER BY sequence DESC LIMIT 1",
            (str(run_id), boundary.as_of_sequence),
        ).fetchone()
        evaluation = (
            _load_event(connection, evaluation_row, budget)
            if evaluation_row is not None
            else None
        )
        return RunPageRead(run, boundary, events, evaluation)


def _load_run(
    connection: sqlite3.Connection, row: sqlite3.Row, budget: ReadBudget
) -> StoredRun:

    issues: list[ReadIssue] = []
    record = None
    context = None
    if row["state_bytes"] > LIMITS.state_bytes or not budget.reserve(
        row["state_bytes"]
    ):
        issues.append(ReadIssue("read_limit_exceeded", "Run 正文超出读取上限"))
    else:
        body = connection.execute(
            "SELECT run_id,schema_version,status,created_at,updated_at,finished_at,last_event_sequence,latest_snapshot_version,state_json,NULL AS recovery_context_json FROM runs WHERE run_id=?",
            (row["run_id"],),
        ).fetchone()
        try:
            raw = parse_json(body["state_json"], budget=budget)
            issues.extend(_missing_state_fields(raw))
            del raw
            record = decode_run(body)
        except (ContentError, CorruptStoredDataError, UnsupportedStorageVersionError):
            issues.append(ReadIssue("state_unsupported", "Run 正文无效或版本不支持"))
    recovery_size = row["recovery_bytes"]
    if recovery_size is not None:
        if recovery_size > LIMITS.recovery_bytes or not budget.reserve(recovery_size):
            issues.append(
                ReadIssue("read_limit_exceeded", "Workspace 登记超出读取上限")
            )
        else:
            recovery = connection.execute(
                "SELECT recovery_context_json FROM runs WHERE run_id=?",
                (row["run_id"],),
            ).fetchone()[0]
            try:
                parse_json(recovery, budget=budget)
                context = decode_recovery_context(recovery)
                if str(context.workspace_spec.run_id) != row["run_id"]:
                    context = None
                    issues.append(
                        ReadIssue("recovery_inconsistent", "Workspace 登记归属不一致")
                    )
            except (ContentError, ValidationError, UnsupportedStorageVersionError):
                issues.append(
                    ReadIssue("recovery_unsupported", "Workspace 登记无效或版本不支持")
                )
    if record is not None and context is not None:
        record = record.model_copy(update={"recovery_context": context})
    return StoredRun(
        _envelope(row),
        record,
        ("local" if context.workspace_spec.kind == "local" else "docker")
        if context is not None
        else "unknown",
        tuple(issues),
    )


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
