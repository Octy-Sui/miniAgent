import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.runtime.events import RunEventType
from miniagent.runtime.store_contracts import CorruptStoredDataError
from miniagent.storage import SQLiteRunStore
from tests.support.storage.sqlite import make_event, make_state


async def history(path: Path) -> tuple[SQLiteRunStore, UUID]:
    store = SQLiteRunStore(path)
    await store.initialize()
    state = make_state()
    await store.create(state)
    for sequence in range(1, 35):
        await store.append(
            make_event(
                run_id=state.identity.run_id,
                type=(
                    RunEventType.RUN_STARTED
                    if sequence in {2, 17, 32}
                    else RunEventType.MODEL_REQUESTED
                ),
            ),
            expected_sequence=sequence - 1,
        )
    return store, state.identity.run_id


@pytest.mark.parametrize("boundary", [15, 16, 17, 32, 33])
async def test_fixed_boundary_pages(tmp_path: Path, boundary: int) -> None:
    store, run_id = await history(tmp_path / "runs.db")
    page = await store.list_events(run_id, through_sequence=boundary, limit=16)
    assert [event.sequence for event in page] == list(range(1, min(boundary, 16) + 1))
    from miniagent.runtime.history import iter_run_events

    assert [
        event.sequence
        async for event in iter_run_events(store, run_id, through_sequence=boundary)
    ] == list(range(1, boundary + 1))


async def test_sql_filter_does_not_decode_unselected_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "runs.db"
    store, run_id = await history(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET payload_json = '{}' WHERE sequence = 1"
        )
    statements: list[str] = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    selected = await store.list_events(
        run_id, through_sequence=32, types=(RunEventType.RUN_STARTED,), limit=16
    )
    assert [event.sequence for event in selected] == [2, 17, 32]
    event_queries = [sql for sql in statements if "SELECT * FROM run_events" in sql]
    assert len(event_queries) == 1
    assert "sequence <= 32" in event_queries[0]
    assert "type IN ('run_started')" in event_queries[0]
    assert "LIMIT 16" in event_queries[0]
    assert await store.list_events(run_id, types=(), through_sequence=32) == []
    from miniagent.runtime.history import iter_run_events

    assert [
        event.sequence
        async for event in iter_run_events(
            store, run_id, through_sequence=32, types=(RunEventType.RUN_STARTED,)
        )
    ] == [2, 17, 32]
    assert (
        await store.get_event(run_id, selected[-1].event_id, through_sequence=31)
        is None
    )
    other = make_state()
    await store.create(other)
    assert (
        await store.get_event(
            other.identity.run_id, selected[0].event_id, through_sequence=32
        )
        is None
    )
    assert (
        await store.get_event(run_id, selected[0].event_id, through_sequence=32)
        == selected[0]
    )


@pytest.mark.parametrize("gap", [16, 17])
async def test_unfiltered_scan_rejects_page_boundary_gap(
    tmp_path: Path, gap: int
) -> None:
    path = tmp_path / "runs.db"
    store, run_id = await history(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER run_events_no_delete")
        connection.execute("DELETE FROM run_events WHERE sequence = ?", (gap,))
    from miniagent.runtime.history import iter_run_events

    with pytest.raises(CorruptStoredDataError, match="sequence"):
        async for _ in iter_run_events(store, run_id, through_sequence=33):
            pass


@pytest.mark.parametrize(
    "kwargs", [{"through_sequence": -1}, {"after_sequence": -1}, {"limit": 0}]
)
async def test_query_rejects_invalid_bounds(tmp_path: Path, kwargs: dict) -> None:
    store, run_id = await history(tmp_path / "runs.db")
    with pytest.raises(ValueError):
        await store.list_events(run_id, **kwargs)
    assert await store.list_events(run_id, after_sequence=17, through_sequence=16) == []
