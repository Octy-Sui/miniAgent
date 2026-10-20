import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from miniagent.dashboard.queries.connection import read_transaction, validate_database
from miniagent.dashboard.reads import ReadBudget, ReadFailure
from tests.support.dashboard.builders import make_database
from tests.support.dashboard.observation import capture_read_only


def test_missing_database_is_never_created(tmp_path: Path) -> None:
    missing = tmp_path / "absent" / "runs.sqlite3"
    with (
        pytest.raises(ReadFailure, match="database_missing"),
        read_transaction(missing, ReadBudget(5, monotonic=lambda: 0)),
    ):
        pytest.fail("missing DB opened")
    assert not missing.exists()
    assert not missing.parent.exists()


def test_read_transaction_is_read_only(database: Path, tmp_path: Path) -> None:

    before = capture_read_only(database, tmp_path / "artifacts")
    statements: list[str] = []
    connections: list[sqlite3.Connection] = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        connections.append(connection)
        return connection

    with read_transaction(
        database, ReadBudget(5, monotonic=lambda: 0), connect=connect
    ) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        assert connection.in_transaction
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM runs")
    assert capture_read_only(database, tmp_path / "artifacts") == before
    assert "BEGIN" in statements
    assert not any("journal_mode" in statement.lower() for statement in statements)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_deadline_interrupts_real_sql(database: Path) -> None:

    now = [0.0]
    started = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)

        def trace(sql: str) -> None:
            if sql.startswith("WITH RECURSIVE"):
                started.append(sql)
                now[0] = 5.0

        connection.set_trace_callback(trace)
        return connection

    with (
        pytest.raises(ReadFailure, match="read_deadline") as error,
        read_transaction(
            database, ReadBudget(5, monotonic=lambda: now[0]), connect=connect
        ) as connection,
    ):
        connection.execute(
            "WITH RECURSIVE t(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM t WHERE x<100000) SELECT sum(x) FROM t"
        ).fetchone()
    assert error.value.status_code == 503
    assert len(started) == 1


async def test_encoded_database_path_is_readable(tmp_path: Path) -> None:

    database = await make_database(tmp_path / "空 格?#.sqlite3")
    validate_database(database)


@pytest.mark.parametrize(("damage",), [("version",), ("table",), ("column",)])
def test_startup_rejects_unsupported_structure(database: Path, damage: str) -> None:

    with closing(sqlite3.connect(database)) as connection:
        if damage == "version":
            connection.execute("PRAGMA user_version=2")
        elif damage == "table":
            connection.execute("DROP TABLE run_snapshots")
        else:
            connection.execute(
                "ALTER TABLE run_events RENAME COLUMN payload_json TO wrong_column"
            )
        connection.commit()
    with pytest.raises(ReadFailure, match="database_unsupported"):
        validate_database(database)
