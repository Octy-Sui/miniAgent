import sqlite3
from uuid import UUID


def select_latest_snapshot(
    connection: sqlite3.Connection,
    run_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM run_snapshots
        WHERE run_id = ? ORDER BY version DESC LIMIT 1
        """,
        (str(run_id),),
    ).fetchone()


def insert_snapshot(
    connection: sqlite3.Connection,
    values: tuple[object, ...],
) -> None:
    connection.execute(
        """
        INSERT INTO run_snapshots (
            run_id, version, snapshot_id, event_sequence,
            schema_version, snapshot_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )


def select_snapshot_version(
    connection: sqlite3.Connection, run_id: UUID, version: int
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM run_snapshots WHERE run_id = ? AND version = ?",
        (str(run_id), version),
    ).fetchone()
