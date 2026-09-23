import sqlite3
from uuid import UUID


def select_approval(
    connection: sqlite3.Connection,
    approval_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM approvals WHERE approval_id = ?",
        (str(approval_id),),
    ).fetchone()


def select_approval_for_run(
    connection: sqlite3.Connection,
    run_id: UUID,
    approval_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM approvals WHERE approval_id = ? AND run_id = ?",
        (str(approval_id), str(run_id)),
    ).fetchone()


def select_pending_approvals(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM approvals
        WHERE outcome IS NULL
        ORDER BY requested_at, approval_id
        """
    ).fetchall()


def insert_approval(
    connection: sqlite3.Connection,
    values: tuple[object, ...],
) -> None:
    connection.execute(
        """
        INSERT INTO approvals (
            approval_id, run_id, decision_id, schema_version,
            request_json, decision_json, requested_at, expires_at,
            request_event_id
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
        """,
        values,
    )


def resolve_approval(
    connection: sqlite3.Connection,
    values: tuple[object, ...],
) -> int:
    cursor = connection.execute(
        """
        UPDATE approvals
        SET outcome = ?, resolution_json = ?,
            resolution_event_id = ?, version = version + 1
        WHERE approval_id = ? AND run_id = ?
            AND version = ? AND outcome IS NULL
        """,
        values,
    )
    return cursor.rowcount


def invalidate_approval(
    connection: sqlite3.Connection,
    values: tuple[object, ...],
) -> int:
    cursor = connection.execute(
        """
        UPDATE approvals
        SET authorization_invalidated_event_id = ?,
            version = version + 1
        WHERE approval_id = ? AND run_id = ? AND version = ?
            AND outcome = ?
            AND authorization_invalidated_event_id IS NULL
        """,
        values,
    )
    return cursor.rowcount


def select_approval_version(
    connection: sqlite3.Connection,
    run_id: UUID,
    approval_id: UUID,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT version FROM approvals WHERE approval_id = ? AND run_id = ?",
        (str(approval_id), str(run_id)),
    ).fetchone()
