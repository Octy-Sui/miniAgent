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
