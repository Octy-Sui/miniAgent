import sqlite3
from contextlib import closing
from pathlib import Path

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.events import read_events
from miniagent.dashboard.queries.runs import read_run_page
from miniagent.dashboard.reads import ReadBudget
from miniagent.runtime.events import EvaluationCompleted, NewRunEvent, RunStarted
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.storage import SQLiteRunStore
from tests.dashboard.builders import make_run
from tests.dashboard.observation import RecordingConnection


async def test_first_page_uses_one_committed_view(database: Path) -> None:
    run_id = await make_run(database, 1)
    page = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert page.boundary.as_of_sequence == 0
    assert page.boundary.latest_snapshot_version == 0
    assert page.run.record is not None
    assert page.run.record.state.identity.run_id == run_id
    assert page.events.items == ()


async def test_oversized_run_preserves_envelope(database: Path) -> None:
    run_id = await make_run(database, 1, task="x" * (20 * 1024 * 1024 + 1))
    page = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert page.run.envelope.run_id == run_id
    assert page.run.record is None
    assert "read_limit_exceeded" in {issue.code for issue in page.run.issues}


async def test_wal_writer_cannot_mix_first_page_facts(database: Path) -> None:

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    for sequence in range(240):
        await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=sequence
        )
    record = await store.get(run_id)
    for version in range(2):
        await store.save(
            NewRunSnapshot(run_id=run_id, event_sequence=240, state=record.state),
            expected_version=version,
        )
    changed = []

    def writer() -> None:
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "UPDATE runs SET state_json=json_set(state_json,'$.usage.input_tokens',999,'$.usage.total_tokens',999),last_event_sequence=241,latest_snapshot_version=3"
            )
            connection.execute(
                "INSERT INTO run_events SELECT run_id,241,?,schema_version,type,occurred_at,payload_json,artifacts_json,step_number,span_id,parent_span_id FROM run_events WHERE sequence=240",
                ("00000000-0000-0000-0000-000000009999",),
            )
            connection.execute(
                "INSERT INTO run_snapshots SELECT run_id,3,?,241,schema_version,json_set(snapshot_json,'$.event_sequence',241,'$.state.usage.input_tokens',999,'$.state.usage.total_tokens',999),created_at FROM run_snapshots WHERE version=2",
                ("00000000-0000-0000-0000-000000008888",),
            )
            connection.commit()
        changed.append(True)

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, factory=RecordingConnection, **kwargs)
        connection.after_first_run_fetch = writer
        return connection

    page = read_run_page(
        database,
        run_id,
        EventFilters(),
        budget=ReadBudget(5, monotonic=lambda: 0),
        connect=connect,
    )
    assert changed == [True]
    assert (page.boundary.as_of_sequence, page.boundary.latest_snapshot_version) == (
        240,
        2,
    )
    assert page.run.record is not None
    assert page.run.record.state.usage.total_tokens == 0
    old = read_events(
        database,
        page.boundary,
        EventFilters(),
        after_sequence=200,
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert len(old.items) == 40
    assert all(event.sequence <= 240 for event in old.items)
    refreshed = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert refreshed.run.record is not None
    assert refreshed.run.record.state.usage.total_tokens == 999
    assert refreshed.boundary.as_of_sequence == 241
    assert refreshed.boundary.latest_snapshot_version == 3


async def test_missing_persisted_usage_is_marked_before_codec_defaults(
    database: Path,
) -> None:

    run_id = await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_remove(state_json,'$.usage.cost_usd','$.definition.config.max_steps')"
        )
        connection.commit()
    page = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    missing = {
        issue.field_path
        for issue in page.run.issues
        if issue.code == "field_not_recorded"
    }
    assert ("usage", "cost_usd") in missing
    assert ("definition", "config", "max_steps") in missing
    assert page.run.record is not None


async def test_last_evaluation_is_read_independently_of_first_batch(
    database: Path,
) -> None:

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    await store.append(
        NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=0
    )
    event = await store.append(
        NewRunEvent(run_id=run_id, payload=EvaluationCompleted(passed=False)),
        expected_sequence=1,
    )
    page = read_run_page(
        database,
        run_id,
        EventFilters(),
        limit=1,
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert page.evaluation_event is not None
    assert page.evaluation_event.event_id == event.event_id
    assert page.events.next_sequence == 1


import json

import pytest

from miniagent.runtime.events import ModelRequested


@pytest.mark.parametrize(("delta",), [(-2,), (-1,), (0,), (1,), (2,)])
async def test_state_utf8_length_gate(database: Path, delta: int) -> None:
    run_id = await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        raw = json.loads(
            connection.execute("SELECT state_json FROM runs").fetchone()[0]
        )
        raw["identity"]["task"] = ""
        encoded = json.dumps(raw, separators=(",", ":"))
        raw["identity"]["task"] = "x" * (
            20 * 1024 * 1024 + delta - len(encoded.encode())
        )
        connection.execute(
            "UPDATE runs SET state_json=?", (json.dumps(raw, separators=(",", ":")),)
        )
        connection.commit()
    page = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert (page.run.record is not None) == (delta <= 0)
    assert ("read_limit_exceeded" in {issue.code for issue in page.run.issues}) == (
        delta > 0
    )


async def test_recovery_error_does_not_discard_valid_state(database: Path) -> None:
    run_id = await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE runs SET recovery_context_json='{}'")
        connection.commit()
    page = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert page.run.record is not None
    assert page.run.workspace_kind == "unknown"
    assert "recovery_unsupported" in {issue.code for issue in page.run.issues}


async def test_actual_returned_structured_bytes_never_exceed_request_budget(
    database: Path,
) -> None:
    from uuid import UUID

    run_id = await make_run(database, 1)
    payload = ModelRequested(provider="test", model="")
    payload = payload.model_copy(
        update={"model": "x" * (1048576 - len(payload.model_dump_json().encode()))}
    )
    text = payload.model_dump_json()
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json) VALUES(?,?,?,3,'model_requested','2026-10-19T00:00:00Z',?,'[]')",
            [(str(run_id), n, str(UUID(int=n + 1)), text) for n in range(1, 66)],
        )
        connection.execute("UPDATE runs SET last_event_sequence=65")
        connection.commit()
    observed = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, factory=RecordingConnection, **kwargs)
        observed.append(connection)
        return connection

    page = read_run_page(
        database,
        run_id,
        EventFilters(),
        budget=ReadBudget(5, monotonic=lambda: 0),
        connect=connect,
    )
    assert len(page.events.items) == 65
    assert sum(item.event is not None for item in page.events.items) == 63
    assert observed[0].structured_bytes <= 67108864
    assert "read_limit_exceeded" in {
        issue.code for issue in page.events.items[-1].issues
    }
