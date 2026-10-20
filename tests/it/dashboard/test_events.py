import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.projections.events import (
    project_events,
    project_tools,
    step_is_partial,
)
from miniagent.dashboard.queries.events import read_events, read_tool_facts
from miniagent.dashboard.reads import ReadBudget
from miniagent.runtime.events import NewRunEvent, RunStarted
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import make_run
from tests.support.dashboard.events import tool_page


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        ("model_responded", {"error_code": "failure"}, True),
        ("model_responded", {"error_code": None}, False),
        ("tool_end", {"status": "error"}, True),
        ("tool_end", {"status": "timeout"}, True),
        ("tool_execution_end", {"status": "denied"}, True),
        ("tool_end", {"status": "success"}, False),
        ("tool_end", {"status": "unknown"}, False),
        ("step_completed", {"failed": True}, True),
        ("step_completed", {"failed": "true"}, False),
        ("evaluation_completed", {"passed": False}, True),
        ("evaluation_completed", {"passed": "false"}, False),
        ("run_finished", {"status": "failed"}, True),
        ("run_finished", {"status": "cancelled"}, False),
        ("compaction_failed", {}, True),
        ("approval_invalidated", {}, True),
        ("approval_resolved", {"outcome": "rejected"}, True),
        ("approval_resolved", {"outcome": "expired"}, True),
        ("approval_resolved", {"outcome": "invalidated"}, True),
        ("approval_resolved", {"outcome": "approved"}, False),
        ("policy_decided", {"decision": {"effect": "deny"}}, False),
        ("unknown", {"failed": True}, False),
    ],
)
async def test_failure_filter_matches_exact_known_facts(
    database: Path, kind: str, payload: dict, expected: bool
) -> None:
    run_id = await make_run(database, 1)
    event_id = UUID(int=101)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json) VALUES(?,1,?,3,?,?,?,?)",
            (
                str(run_id),
                str(event_id),
                kind,
                "2026-10-19T00:00:00Z",
                json.dumps({"type": kind} | payload),
                "[]",
            ),
        )
        connection.execute("UPDATE runs SET last_event_sequence=1")
        connection.commit()
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=1,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    batch = read_events(
        database,
        boundary,
        EventFilters(failures_only=True),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert [event.event_id for event in batch.items] == ([event_id] if expected else [])
    assert batch.next_sequence is None


async def test_tool_ids_do_not_cross_steps(database: Path) -> None:
    boundary = await tool_page(database)
    batch = read_events(
        database, boundary, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    tools = project_tools(batch.items)
    assert [(tool.step_number, tool.tool_call_id) for tool in tools] == [
        (1, "same"),
        (2, "same"),
    ]


async def test_success_and_truncation_are_independent(database: Path) -> None:
    boundary = await tool_page(database)
    batch = read_events(
        database,
        boundary,
        EventFilters(types=("tool_end",)),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    facts = read_tool_facts(
        database,
        boundary,
        ((1, "same"), (2, "same")),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    tools = project_tools(batch.items, facts=facts)
    assert [(tool.status, tool.completeness, tool.exit_code) for tool in tools] == [
        ("success", "truncated", 0),
        ("success", "truncated", 0),
    ]
    assert all(
        "source_missing" not in {issue.code for issue in tool.issues} for tool in tools
    )
    assert step_is_partial(batch.items, 1)
    views = project_events(batch)
    assert "private-value" not in json.dumps([view.payload for view in views])
    assert "visible" in json.dumps([view.payload for view in views])


async def test_sparse_matches_are_filtered_before_limit(database: Path) -> None:
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    for sequence in range(220):
        await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=sequence
        )
    with closing(sqlite3.connect(database)) as connection:
        # Corrupt matched envelopes are inserted at the persistence boundary.
        connection.execute(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json) VALUES(?,221,?,999,'compaction_failed','2026-10-19T00:00:00Z','{}','[]')",
            (str(run_id), str(UUID(int=999))),
        )
        connection.execute("UPDATE runs SET last_event_sequence=221")
        connection.commit()
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=221,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    batch = read_events(
        database,
        boundary,
        EventFilters(failures_only=True),
        limit=1,
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert [event.sequence for event in batch.items] == [221]
    assert batch.items[0].event is None
    assert batch.next_sequence is None
