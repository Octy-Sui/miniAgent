import json
import sqlite3
import threading


class SQLiteWriteBarrier:
    """Pause after a real event INSERT, while its transaction is undecided."""

    def __init__(self, event_type: str, *, command_only: bool = False) -> None:
        self.event_type = event_type
        self.command_only = command_only
        self.entered = threading.Event()
        self.release = threading.Event()

    def connector(self, connect):
        barrier = self

        class Connection(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                cursor = super().execute(sql, parameters)
                if (
                    "INSERT INTO run_events" in sql
                    and len(parameters) > 4
                    and parameters[4] == barrier.event_type
                    and (
                        not barrier.command_only
                        or json.loads(parameters[6]).get("command") is not None
                    )
                ):
                    barrier.entered.set()
                    if not barrier.release.wait(10):
                        raise TimeoutError("SQLite write barrier was not released")
                return cursor

        def open_connection(*args, **kwargs):
            return connect(*args, **kwargs, factory=Connection)

        return open_connection
