import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self


@dataclass(frozen=True)
class ReadOnlySnapshot:
    logical_rows: dict[str, tuple[tuple[object, ...], ...]]
    schema: tuple[tuple[object, ...], ...]
    artifacts: dict[str, str]


def capture_read_only(database: Path, artifact_root: Path) -> ReadOnlySnapshot:
    with closing(
        sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        schema = tuple(
            connection.execute("SELECT * FROM sqlite_master ORDER BY type,name")
        )
        version = connection.execute("PRAGMA user_version").fetchone()
        rows = {}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ):
            identifier = '"' + table.replace('"', '""') + '"'
            rows[table] = tuple(
                sorted(connection.execute(f"SELECT * FROM {identifier}"), key=repr)
            )
    artifacts = {}
    if artifact_root.exists():
        for path in artifact_root.rglob("*"):
            relative = str(path.relative_to(artifact_root))
            if path.is_dir():
                artifacts[relative] = "directory"
            else:
                with path.open("rb") as stream:
                    artifacts[relative] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
    return ReadOnlySnapshot(rows, (*schema, ("user_version", *version)), artifacts)


class RecordingCursor(sqlite3.Cursor):
    sql = ""

    def execute(self, sql: str, parameters: Any = ()) -> Self:
        self.sql = sql
        super().execute(sql, parameters)
        return self

    def fetchone(self) -> Any:
        row = super().fetchone()
        connection = self.connection
        if isinstance(connection, RecordingConnection) and row is not None:
            connection.record_fetch(self.sql, row)
        return row

    def fetchall(self) -> list[Any]:
        rows = super().fetchall()
        connection = self.connection
        if isinstance(connection, RecordingConnection):
            for row in rows:
                connection.record_fetch(self.sql, row)
        return rows


class RecordingConnection(sqlite3.Connection):
    after_first_run_fetch: Callable[[], None] | None = None
    fetched_bytes = 0
    structured_bytes = 0

    def execute(self, sql: str, parameters: Any = ()) -> RecordingCursor:
        cursor = self.cursor(factory=RecordingCursor)
        assert isinstance(cursor, RecordingCursor)
        return cursor.execute(sql, parameters)

    def record_fetch(self, sql: str, row: Sequence[Any]) -> None:
        if isinstance(row, sqlite3.Row):
            for key in row.keys():  # noqa: SIM118 -- sqlite3.Row iterates values.
                if key.endswith("_json") and isinstance(row[key], str):
                    self.structured_bytes += len(row[key].encode("utf-8"))
        self.fetched_bytes += sum(
            len(value.encode("utf-8")) for value in row if isinstance(value, str)
        )
        if "FROM runs WHERE" in sql and self.after_first_run_fetch is not None:
            hook = self.after_first_run_fetch
            self.after_first_run_fetch = None
            hook()
