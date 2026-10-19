import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.events import locate_event, read_events
from miniagent.dashboard.queries.snapshots import read_snapshot
from miniagent.dashboard.reads import ReadBudget, ReadFailure
from miniagent.models.messages import UserMessage
from miniagent.runtime.events import NewRunEvent, RunStarted
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.storage import SQLiteRunStore
from tests.dashboard.builders import make_run


async def test_old_boundary_never_loads_future_sources(database: Path) -> None:
    run_id = await make_run(database, 1)
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=1,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    with pytest.raises(ReadFailure, match="boundary_invalid"):
        read_events(
            database,
            boundary,
            EventFilters(),
            budget=ReadBudget(5, monotonic=lambda: 0),
        )


async def test_event_pages_freeze_at_signed_sequence(database: Path) -> None:

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    first = await store.append(
        NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=0
    )
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=1,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    await store.append(
        NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=1
    )
    batch = read_events(
        database, boundary, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert [event.event_id for event in batch.items] == [first.event_id]
    assert batch.items[0].event is not None
    assert batch.next_sequence is None


async def test_snapshot_only_returns_requested_messages(database: Path) -> None:

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    record = await store.get(run_id)
    conversation = record.state.conversation.model_copy(
        update={
            "messages": tuple(UserMessage(content=f"message-{n}") for n in range(120))
        }
    )
    state = record.state.model_copy(update={"conversation": conversation})

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE runs SET state_json=?", (state.model_dump_json(),))
        connection.commit()
    await store.save(
        NewRunSnapshot(run_id=run_id, event_sequence=0, state=state), expected_version=0
    )
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=0,
        latest_snapshot_version=1,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    source = read_snapshot(
        database,
        boundary,
        1,
        start=10,
        end=12,
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert [message.content for message in source.messages] == [
        "message-10",
        "message-11",
    ]
    assert source.message_count == 120
    for start, end in [(-1, 2), (10, 9), (120, 121), (0, 101), (10, 10)]:
        with pytest.raises(ReadFailure):
            read_snapshot(
                database,
                boundary,
                1,
                start=start,
                end=end,
                budget=ReadBudget(5, monotonic=lambda: 0),
            )


async def test_locator_returns_at_most_twenty_neighbors_each_side(
    database: Path,
) -> None:

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    target = None
    for sequence in range(60):
        event = await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=sequence
        )
        if event.sequence == 30:
            target = event.event_id
    assert target is not None
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=60,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    located = locate_event(
        database, boundary, target, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert [event.sequence for event in located.items] == list(range(10, 51))


async def test_future_event_source_is_outside_boundary(database: Path) -> None:
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    future = await store.append(
        NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=0
    )
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=0,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    with pytest.raises(ReadFailure) as error:
        locate_event(
            database,
            boundary,
            future.event_id,
            budget=ReadBudget(5, monotonic=lambda: 0),
        )
    assert error.value.status_code == 422


async def test_empty_snapshot_and_independent_bounds(database: Path) -> None:
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    record = await store.get(run_id)
    await store.save(
        NewRunSnapshot(run_id=run_id, event_sequence=0, state=record.state),
        expected_version=0,
    )
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=0,
        latest_snapshot_version=1,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    source = read_snapshot(
        database, boundary, 1, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert (source.start, source.end, source.message_count, source.messages) == (
        0,
        0,
        0,
        (),
    )
    with pytest.raises(ReadFailure):
        read_snapshot(
            database,
            boundary.model_copy(update={"latest_snapshot_version": 0}),
            1,
            budget=ReadBudget(5, monotonic=lambda: 0),
        )
    await store.append(
        NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=0
    )
    await store.save(
        NewRunSnapshot(run_id=run_id, event_sequence=1, state=record.state),
        expected_version=1,
    )
    with pytest.raises(ReadFailure):
        read_snapshot(
            database,
            boundary.model_copy(update={"latest_snapshot_version": 2}),
            2,
            budget=ReadBudget(5, monotonic=lambda: 0),
        )
