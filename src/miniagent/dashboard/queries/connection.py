import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.reads import ReadBudget, ReadFailure

ConnectionFactory = Callable[..., sqlite3.Connection]


@contextmanager
def read_transaction(
    database: Path,
    budget: ReadBudget,
    *,
    connect: ConnectionFactory = sqlite3.connect,
) -> Iterator[sqlite3.Connection]:
    if not database.is_file():
        raise ReadFailure(503, "database_missing")
    budget.check()
    connection = connect(
        database.expanduser().resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(
            lambda: int(budget.monotonic() >= budget.deadline), 1000
        )
        connection.execute("BEGIN")
        yield connection
        budget.check()
    except sqlite3.OperationalError as exc:
        code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise ReadFailure(503, "database_busy") from exc
        if code == sqlite3.SQLITE_INTERRUPT:
            raise ReadFailure(503, "read_deadline") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)
        try:
            connection.rollback()
        finally:
            connection.close()


def validate_database(
    database: Path, *, connect: ConnectionFactory = sqlite3.connect
) -> None:
    required = {
        "runs": {
            "run_id",
            "schema_version",
            "status",
            "state_json",
            "recovery_context_json",
            "last_event_sequence",
            "latest_snapshot_version",
            "created_at",
            "updated_at",
            "finished_at",
        },
        "run_events": {
            "run_id",
            "sequence",
            "event_id",
            "schema_version",
            "type",
            "occurred_at",
            "payload_json",
            "artifacts_json",
            "step_number",
            "span_id",
            "parent_span_id",
        },
        "run_snapshots": {
            "run_id",
            "version",
            "snapshot_id",
            "event_sequence",
            "schema_version",
            "snapshot_json",
            "created_at",
        },
    }
    with read_transaction(
        database, ReadBudget(time.monotonic() + LIMITS.read_seconds), connect=connect
    ) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 3:
            raise ReadFailure(503, "database_unsupported")
        for table, columns in required.items():
            actual = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not columns <= actual:
                raise ReadFailure(503, "database_unsupported")
        if connection.execute(
            "SELECT json_valid('{}'), json_extract('{\"v\":1}', '$.v')"
        ).fetchone()[:] != (1, 1):
            raise ReadFailure(503, "database_unsupported")
