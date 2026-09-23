import sqlite3
from typing import Any
from uuid import UUID


def select_events(
    connection: sqlite3.Connection,
    run_id: UUID,
    *,
    after_sequence: int,
    limit: int | None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM run_events WHERE run_id = ? AND sequence > ? "
        "ORDER BY sequence ASC"
    )
    parameters: tuple[Any, ...] = (str(run_id), after_sequence)
    if limit is not None:
        sql += " LIMIT ?"
        parameters += (limit,)
    return connection.execute(sql, parameters).fetchall()


def insert_event(connection: sqlite3.Connection, values: tuple[Any, ...]) -> None:
    connection.execute(
        """
        INSERT INTO run_events (
            run_id, sequence, event_id, schema_version, type,
            occurred_at, payload_json, artifacts_json,
            step_number, span_id, parent_span_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values,
    )
