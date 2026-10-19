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


import os
import sys
from multiprocessing.connection import Connection
from types import FrameType


class RecordingOpen:
    def __init__(
        self,
        root: Path,
        *,
        pause_at: tuple[str, ...] | None = None,
        channel: Connection | None = None,
    ) -> None:
        self.pause_at = pause_at
        self.channel = channel
        self.paused = False
        self.root = root.resolve()
        self.nodes: dict[int, tuple[str, ...]] = {}
        self.opened: list[tuple[int, tuple[str, ...], int, int]] = []

    def __call__(
        self, path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        descriptor = os.open(path, flags, mode, dir_fd=dir_fd)
        relative = (
            () if Path(path) == self.root else (*self.nodes.get(dir_fd, ()), str(path))
        )
        self.nodes[descriptor] = relative
        info = os.fstat(descriptor)
        self.opened.append((descriptor, relative, info.st_dev, info.st_ino))
        if relative == self.pause_at and not self.paused and self.channel is not None:
            self.paused = True
            self.channel.send(
                {"opened": (descriptor, relative, info.st_dev, info.st_ino)}
            )
            if not self.channel.poll(10):
                os.close(descriptor)
                raise TimeoutError("FD observation handshake timed out")
            if self.channel.recv() != "continue":
                os.close(descriptor)
                raise ValueError("invalid FD observation handshake")
        return descriptor

    def content_fd(self, descriptor: object) -> bool:
        path = self.nodes.get(descriptor, ()) if isinstance(descriptor, int) else ()
        return len(path) == 3 and path[0] == "content" and len(path[2]) == 64


def probe_content(
    root: str,
    run_id: str,
    artifact_id: str,
    channel,
    pause_at: tuple[str, ...] | None = None,
) -> None:
    from uuid import UUID

    from miniagent.dashboard.artifacts import ArtifactReader
    from miniagent.dashboard.reads import ReadBudget, ReadFailure

    opening = RecordingOpen(Path(root), pause_at=pause_at, channel=channel)
    reads = []
    hashes = []

    def profile(frame: FrameType, event: str, arg: object):
        descriptor = frame.f_locals.get("descriptor")
        if event == "c_call" and opening.content_fd(descriptor):
            if arg is os.read:
                reads.append((descriptor, frame.f_locals.get("read_size")))
            elif getattr(arg, "__name__", None) == "update":
                hashes.append(descriptor)

    previous = sys.getprofile()
    sys.setprofile(profile)
    try:
        result = ArtifactReader(Path(root), open_fd=opening).read_content(
            UUID(run_id),
            UUID(artifact_id),
            budget=ReadBudget(__import__("time").monotonic() + 5),
        )
        data = {
            "content": result.content,
            "issues": [issue.code for issue in result.issues],
        }
    except ReadFailure as exc:
        data = {"error": exc.code, "status": exc.status_code}
    finally:
        sys.setprofile(previous)
    leaked = []
    for descriptor, _, device, inode in opening.opened:
        try:
            info = os.fstat(descriptor)
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == (device, inode):
            leaked.append(descriptor)
    channel.send(
        {
            **data,
            "reads": reads,
            "hashes": hashes,
            "leaked": leaked,
            "opened": opening.opened,
        }
    )
    channel.close()
