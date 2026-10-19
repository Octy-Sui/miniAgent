import sqlite3
from pathlib import Path
from uuid import UUID

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.queries.connection import (
    ConnectionFactory,
    _validate_boundary,
    read_transaction,
)
from miniagent.dashboard.reads import (
    ReadBudget,
    ReadFailure,
    ReadIssue,
    SnapshotSlice,
    StoredSnapshot,
    _missing_state_fields,
)
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    UnsupportedStorageVersionError,
)
from miniagent.storage.codecs import decode_snapshot


def read_snapshot(
    database: Path,
    boundary: PageBoundary,
    version: int,
    *,
    start: int = 0,
    end: int | None = None,
    budget: ReadBudget,
    connect: ConnectionFactory = sqlite3.connect,
) -> SnapshotSlice:
    if (
        version < 1
        or version > boundary.latest_snapshot_version
        or start < 0
        or (end is not None and (end < start or end - start > 100))
    ):
        raise ReadFailure(422, "snapshot_range_invalid")
    with read_transaction(database, budget, connect=connect) as connection:
        _validate_boundary(connection, boundary)
        stored = _read_snapshot_record(connection, boundary, version, budget)
        snapshot = stored.snapshot
        if snapshot is None:
            return SnapshotSlice(
                stored, start, end if end is not None else start, None, ()
            )
        messages = snapshot.state.conversation.messages
        stop = min(start + 100, len(messages)) if end is None else end
        if (
            stop > len(messages)
            or start > len(messages)
            or (start == stop and len(messages) != 0)
            or (len(messages) == 0 and (start, stop) != (0, 0))
        ):
            raise ReadFailure(422, "snapshot_range_invalid")
        return SnapshotSlice(stored, start, stop, len(messages), messages[start:stop])


def _read_snapshot_record(
    connection: sqlite3.Connection,
    boundary: PageBoundary,
    version: int,
    budget: ReadBudget,
    *,
    envelope: sqlite3.Row | None = None,
) -> StoredSnapshot:
    if not 1 <= version <= boundary.latest_snapshot_version:
        raise ReadFailure(422, "snapshot_range_invalid")
    row = envelope
    if row is None:
        row = connection.execute(
            "SELECT snapshot_id,run_id,version,event_sequence,length(CAST(snapshot_json AS BLOB)) AS size FROM run_snapshots WHERE run_id=? AND version=?",
            (str(boundary.run_id), version),
        ).fetchone()
    if row is None:
        raise ReadFailure(404, "snapshot_missing")
    if row["event_sequence"] > boundary.as_of_sequence:
        raise ReadFailure(422, "boundary_invalid")
    issues: list[ReadIssue] = []
    snapshot = None
    if row["size"] > LIMITS.snapshot_bytes or not budget.reserve(row["size"]):
        issues.append(ReadIssue("read_limit_exceeded", "Snapshot 正文超出读取上限"))
    else:
        body = connection.execute(
            "SELECT * FROM run_snapshots WHERE run_id=? AND version=?",
            (str(boundary.run_id), version),
        ).fetchone()
        try:
            raw = parse_json(body["snapshot_json"], budget=budget)
            if isinstance(raw, dict):
                issues.extend(_missing_state_fields(raw.get("state")))
            del raw
            snapshot = decode_snapshot(body)
        except (
            ContentError,
            CorruptStoredDataError,
            UnsupportedStorageVersionError,
        ):
            issues.append(
                ReadIssue("snapshot_unsupported", "Snapshot 正文无效或版本不支持")
            )
    stored = StoredSnapshot(
        UUID(row["snapshot_id"]),
        boundary.run_id,
        version,
        row["event_sequence"],
        snapshot,
        tuple(issues),
    )
    return stored
