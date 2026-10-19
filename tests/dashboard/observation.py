import hashlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


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
