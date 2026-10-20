import sqlite3
import threading


class SQLiteWriteBarrier:
    """Pause after a real event INSERT, while its transaction is undecided."""

    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
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
                ):
                    barrier.entered.set()
                    if not barrier.release.wait(10):
                        raise TimeoutError("SQLite write barrier was not released")
                return cursor

        def open_connection(*args, **kwargs):
            return connect(*args, **kwargs, factory=Connection)

        return open_connection
