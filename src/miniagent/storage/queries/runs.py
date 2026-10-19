import sqlite3
from uuid import UUID


def select_run(connection: sqlite3.Connection, run_id: UUID) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (str(run_id),),
    ).fetchone()


def run_exists(connection: sqlite3.Connection, run_id: UUID) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        is not None
    )


def latest_positions(
    connection: sqlite3.Connection, run_id: UUID
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT last_event_sequence, latest_snapshot_version
        FROM runs WHERE run_id = ?
        """,
        (str(run_id),),
    ).fetchone()


def insert_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    state_json: str,
    recovery_context_json: str | None,
    created_at: str,
    updated_at: str,
    finished_at: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO runs (
            run_id, schema_version, status, state_json,
            recovery_context_json, created_at, updated_at, finished_at
        ) VALUES (?, 3, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            status,
            state_json,
            recovery_context_json,
            created_at,
            updated_at,
            finished_at,
        ),
    )


def update_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    status: str,
    state_json: str,
    recovery_context_json: str | None,
    last_event_sequence: int,
    latest_snapshot_version: int,
    updated_at: str,
    finished_at: str | None,
) -> int:
    cursor = connection.execute(
        """
        UPDATE runs
        SET status = ?, state_json = ?, recovery_context_json = ?,
            last_event_sequence = ?, latest_snapshot_version = ?,
            updated_at = ?, finished_at = ?
        WHERE run_id = ?
        """,
        (
            status,
            state_json,
            recovery_context_json,
            last_event_sequence,
            latest_snapshot_version,
            updated_at,
            finished_at,
            run_id,
        ),
    )
    return cursor.rowcount


def update_event_sequence(
    connection: sqlite3.Connection,
    run_id: UUID,
    sequence: int,
) -> None:
    connection.execute(
        "UPDATE runs SET last_event_sequence = ? WHERE run_id = ?",
        (sequence, str(run_id)),
    )


def update_snapshot_version(
    connection: sqlite3.Connection,
    run_id: UUID,
    version: int,
) -> None:
    connection.execute(
        "UPDATE runs SET latest_snapshot_version = ? WHERE run_id = ?",
        (version, str(run_id)),
    )
