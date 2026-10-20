import json
import sqlite3
from time import monotonic

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.events import read_events, read_tool_facts
from miniagent.dashboard.reads import ReadBudget
from tests.support.dashboard.events import tool_page


async def test_dashboard_keeps_metadata_for_large_event(database):
    boundary = await tool_page(database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT event_id,payload_json FROM run_events WHERE sequence=2"
        ).fetchone()
        payload = json.loads(row[1])
        payload["result"]["content"] = "x" * (2 * 1024 * 1024)
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET payload_json=? WHERE sequence=2",
            (json.dumps(payload),),
        )
    page = read_events(
        database,
        boundary,
        EventFilters(tool_name="shell"),
        budget=ReadBudget(deadline=monotonic() + 5),
    )
    event = next(item for item in page.items if str(item.event_id) == row[0])
    assert event.type == "tool_end"
    assert event.event is None
    assert any(issue.code == "read_limit_exceeded" for issue in event.issues)
    facts = read_tool_facts(
        database, boundary, ((1, "same"),), budget=ReadBudget(deadline=monotonic() + 5)
    )
    assert facts.counts[(1, "same", "tool_end")] == 1
    assert any(str(item.event_id) == row[0] for item in facts.events)


async def test_scope_projection_releases_validated_payloads(database):
    from miniagent.dashboard.queries.events import read_scope_facts

    boundary = await tool_page(database)
    facts = read_scope_facts(
        database,
        boundary,
        ("0000000000000001", "0000000000000002"),
        budget=ReadBudget(deadline=monotonic() + 5),
    )
    assert all(scope.loaded for scope in facts.scopes.values())
    assert all(
        item.event is None for scope in facts.scopes.values() for item in scope.events
    )
    assert {
        item.sequence for scope in facts.scopes.values() for item in scope.events
    } == {1, 2, 3, 4}


async def test_dashboard_body_lookup_keeps_frozen_boundary(database):
    from miniagent.dashboard.queries.events import read_scope_facts

    boundary = await tool_page(database)
    statements = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    read_scope_facts(
        database,
        boundary,
        ("0000000000000001",),
        budget=ReadBudget(deadline=monotonic() + 5),
        connect=connect,
    )
    body_queries = [sql for sql in statements if "SELECT * FROM run_events" in sql]
    assert body_queries
    assert all("sequence<=4" in sql for sql in body_queries)


async def many_tools(database):
    from miniagent.runtime.events import NewRunEvent
    from miniagent.storage import SQLiteRunStore

    boundary = await tool_page(database)
    store = SQLiteRunStore(database)
    originals = await store.list_events(boundary.run_id, through_sequence=4, limit=2)
    sequence = 4
    for step in range(3, 16):
        for event in originals:
            await store.append(
                NewRunEvent(
                    run_id=boundary.run_id,
                    payload=event.payload,
                    step_number=step,
                    span_id=f"{step:016x}",
                ),
                expected_sequence=sequence,
            )
            sequence += 1
    return boundary.model_copy(update={"as_of_sequence": sequence})


async def test_tool_fact_page_caps_full_bodies_without_losing_metadata(database):
    boundary = await many_tools(database)
    facts = read_tool_facts(
        database,
        boundary,
        tuple((step, "same") for step in range(1, 16)),
        budget=ReadBudget(deadline=monotonic() + 5),
    )
    assert len(facts.events) == 30
    assert sum(item.event is not None for item in facts.events) <= 16
    assert any(issue.code == "not_loaded" for issue in facts.issues)


async def test_located_event_keeps_target_inside_body_cap(database):
    from miniagent.dashboard.queries.events import locate_event
    from miniagent.storage import SQLiteRunStore

    boundary = await many_tools(database)
    target = (
        await SQLiteRunStore(database).list_events(
            boundary.run_id, after_sequence=15, through_sequence=16, limit=1
        )
    )[0]
    page = locate_event(
        database, boundary, target.event_id, budget=ReadBudget(deadline=monotonic() + 5)
    )
    assert len(page.items) == 30
    assert sum(item.event is not None for item in page.items) <= 16
    assert (
        next(item for item in page.items if item.event_id == target.event_id).event
        is not None
    )


async def test_compaction_attempt_group_caps_bodies_and_keeps_metadata(database):
    from datetime import UTC, datetime
    from uuid import uuid4

    from miniagent.dashboard.queries.events import locate_event, read_compaction_sources
    from miniagent.models.protocol import ModelRequest
    from miniagent.runtime.events import (
        CompactionAttempt,
        CompactionAttemptRecorded,
        CompactionFailed,
        CompactionRequested,
        NewRunEvent,
    )
    from miniagent.storage import SQLiteRunStore
    from tests.support.dashboard.builders import make_run
    from tests.support.dashboard.events import PageBoundary

    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    sequence = 0
    first_id = uuid4()
    payloads = [
        CompactionRequested(
            request=ModelRequest(model="test", messages=()),
            attempt_id=first_id,
            provider="test",
            model="test",
            attempt=1,
            source_snapshot_version=None,
            covered_messages=(2, 4),
        )
    ]
    for attempt in range(1, 25):
        attempt_id = first_id if attempt == 1 else uuid4()
        payloads.append(
            CompactionAttemptRecorded(
                attempt_id=attempt_id,
                result=CompactionAttempt(
                    attempt=attempt, attempt_id=attempt_id, error_code="EXTERNAL_ERROR"
                ),
            )
        )
    payloads.append(
        CompactionFailed(code="EXTERNAL_ERROR", reason="attempts exhausted")
    )
    for payload in payloads:
        last = await store.append(
            NewRunEvent(run_id=run_id, payload=payload), expected_sequence=sequence
        )
        sequence += 1
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=sequence,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime.now(UTC),
    )
    visible = locate_event(
        database, boundary, last.event_id, budget=ReadBudget(deadline=monotonic() + 5)
    )
    facts = read_compaction_sources(
        database,
        boundary,
        version=1,
        visible_events=visible.items,
        budget=ReadBudget(deadline=monotonic() + 5),
    )
    assert len(facts.events) == len(payloads)
    assert sum(item.event is not None for item in facts.events) <= 16
    assert any(
        item.event is None and any(issue.code == "not_loaded" for issue in item.issues)
        for item in facts.events
    )
