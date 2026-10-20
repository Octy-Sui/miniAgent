import sqlite3
from typing import Any
from uuid import UUID


def select_events(
    connection: sqlite3.Connection,
    run_id: UUID,
    *,
    after_sequence: int,
    limit: int | None,
    through_sequence: int | None = None,
    types: tuple[str, ...] | None = None,
) -> list[sqlite3.Row]:
    sql = "SELECT * FROM run_events WHERE run_id = ? AND sequence > ? "
    parameters: tuple[Any, ...] = (str(run_id), after_sequence)
    if through_sequence is not None:
        sql += " AND sequence <= ?"
        parameters += (through_sequence,)
    if types is not None:
        if not types:
            return []
        sql += " AND type IN (" + ",".join("?" for _ in types) + ")"
        parameters += types
    sql += " ORDER BY sequence ASC"
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
