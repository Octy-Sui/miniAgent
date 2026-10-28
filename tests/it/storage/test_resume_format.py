import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.events import NewRunEvent, RunStarted
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.runtime.state import create_run_state
from miniagent.runtime.store_contracts import RunCommit, UnsupportedStorageVersionError
from miniagent.storage.sqlite import SQLiteRunStore
from tests.support.cli.agent import DEFAULT_AGENT


def state():
    return create_run_state(
        run_id=uuid4(), task="task", agent_spec=DEFAULT_AGENT, config=RuntimeConfig()
    )


async def populated(path: Path):
    store = SQLiteRunStore(path)
    await store.initialize()
    initial = state()
    context = RunRecoveryContext(
        workspace_id="workspace",
        workspace_spec=persist_workspace_spec(
            LocalWorkspaceSpec(
                run_id=initial.identity.run_id,
                options=LocalOptions(source_dir=path.parent),
            )
        ),
    )
    await store.create(initial, recovery_context=context)
    await store.append(
        NewRunEvent(run_id=initial.identity.run_id, payload=RunStarted()),
        expected_sequence=0,
    )
    await store.save(
        NewRunSnapshot(
            run_id=initial.identity.run_id,
            state=initial,
            recovery_context=context,
            event_sequence=1,
        ),
        expected_version=0,
    )
    return store, initial


async def test_current_schema_round_trip(tmp_path):
    """TC-U2-01: current writers and readers agree on every envelope."""
    database = tmp_path / "runs.sqlite3"
    _, initial = await populated(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT schema_version FROM runs").fetchone()[0] == 3
    reader = SQLiteRunStore(database, read_only=True)
    await reader.inspect_schema()
    assert (await reader.get(initial.identity.run_id)).state == initial
    assert (await reader.list_events(initial.identity.run_id))[0].schema_version == 3
    snapshot = await reader.load_latest(initial.identity.run_id)
    assert snapshot is not None and snapshot.schema_version == 3


@pytest.mark.parametrize(
    ("kind", "version"),
    [
        ("database", 2),
        ("database", 4),
        ("run", 2),
        ("run", 4),
        ("snapshot", 2),
        ("snapshot", 4),
        ("event", 2),
        ("event", 4),
        ("recovery", 1),
        ("recovery", 3),
        ("application", 2),
    ],
)
async def test_unsupported_format_is_untouched(tmp_path, kind, version):
    """TC-U2-02..09: reject each incompatible format without rewriting it."""
    database = tmp_path / "runs.sqlite3"
    _, initial = await populated(database)
    with sqlite3.connect(database) as connection:
        if kind == "database":
            connection.execute(f"PRAGMA user_version = {version}")
        elif kind in ("run", "event", "snapshot"):
            table = {"run": "runs", "event": "run_events", "snapshot": "run_snapshots"}[
                kind
            ]
            if kind != "run":
                connection.execute(f"DROP TRIGGER {table}_no_update")
            connection.execute(f"UPDATE {table} SET schema_version = ?", (version,))
        else:
            context = json.loads(
                connection.execute("SELECT recovery_context_json FROM runs").fetchone()[
                    0
                ]
            )
            if kind == "recovery":
                context["schema_version"] = version
            else:
                context["application"] = {
                    "kind": "cli_application",
                    "schema_version": version,
                }
            connection.execute(
                "UPDATE runs SET recovery_context_json = ?", (json.dumps(context),)
            )
    before_bytes = database.read_bytes()
    reader = SQLiteRunStore(database, read_only=True)
    with pytest.raises(UnsupportedStorageVersionError, match="schema version"):
        if kind == "database":
            await reader.inspect_schema()
        elif kind == "event":
            await reader.list_events(initial.identity.run_id)
        elif kind == "snapshot":
            await reader.load_latest(initial.identity.run_id)
        else:
            await reader.get(initial.identity.run_id)
    assert database.read_bytes() == before_bytes


async def test_read_only_missing_database_creates_nothing(tmp_path):
    """TC-U18-05: dry-run must not create missing database or parent directories."""
    missing_database = tmp_path / "missing" / "runs.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        await SQLiteRunStore(missing_database, read_only=True).inspect_schema()
    assert not missing_database.parent.exists()


async def test_read_only_open_preserves_database_and_journal_mode(tmp_path):
    """TC-U18-06: read-only inspection leaves a DELETE-mode database unchanged."""
    database = tmp_path / "special?# path.sqlite3"
    _, initial = await populated(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
    before_bytes = database.read_bytes()
    reader = SQLiteRunStore(database, read_only=True)
    await reader.inspect_schema()
    assert (await reader.get(initial.identity.run_id)).state == initial
    await reader.list_events(initial.identity.run_id)
    await reader.load_latest(initial.identity.run_id)
    assert database.read_bytes() == before_bytes
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize(
    ("operation",), [("initialize",), ("create",), ("commit",), ("append",), ("save",)]
)
async def test_read_only_rejects_all_mutations(tmp_path, operation):
    reader = SQLiteRunStore(tmp_path / "missing.sqlite3", read_only=True)
    initial = state()
    with pytest.raises(PermissionError, match="read.only"):
        if operation == "initialize":
            await reader.initialize()
        elif operation == "create":
            await reader.create(initial)
        elif operation == "append":
            await reader.append(
                NewRunEvent(run_id=initial.identity.run_id, payload=RunStarted()),
                expected_sequence=0,
            )
        elif operation == "save":
            await reader.save(
                NewRunSnapshot(
                    run_id=initial.identity.run_id, state=initial, event_sequence=0
                ),
                expected_version=0,
            )
        else:
            await reader.commit(
                RunCommit(
                    state=initial,
                    events=[
                        NewRunEvent(
                            run_id=initial.identity.run_id, payload=RunStarted()
                        )
                    ],
                    expected_sequence=0,
                )
            )
    assert not reader.path.exists()


async def test_unknown_nonempty_version_zero_database_is_not_initialized(tmp_path):
    database = tmp_path / "other.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    before = database.read_bytes()
    with pytest.raises(UnsupportedStorageVersionError, match="nonempty"):
        await SQLiteRunStore(database).initialize()
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    ("kind",),
    [("snapshot_envelope",), ("snapshot_recovery",), ("snapshot_application",)],
)
async def test_unsupported_nested_snapshot_format_is_rejected(tmp_path, kind):
    database = tmp_path / "runs.sqlite3"
    _, initial = await populated(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER run_snapshots_no_update")
        raw = json.loads(
            connection.execute("SELECT snapshot_json FROM run_snapshots").fetchone()[0]
        )
        if kind == "snapshot_envelope":
            raw["schema_version"] = 2
        elif kind == "snapshot_recovery":
            raw["recovery_context"]["schema_version"] = 1
        else:
            raw["recovery_context"]["application"] = {
                "kind": "cli_application",
                "schema_version": 2,
            }
        connection.execute(
            "UPDATE run_snapshots SET snapshot_json = ?", (json.dumps(raw),)
        )
    with pytest.raises(UnsupportedStorageVersionError, match="schema version"):
        await SQLiteRunStore(database, read_only=True).load_latest(
            initial.identity.run_id
        )
