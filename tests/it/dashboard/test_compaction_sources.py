import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.projections.compaction import project_memory
from miniagent.dashboard.queries.events import read_compaction_sources
from miniagent.dashboard.queries.runs import read_run_page
from miniagent.dashboard.reads import (
    ReadBudget,
)
from miniagent.runtime.context import EventSource, SnapshotSource, VerifiedEvidence
from tests.support.dashboard.builders import memory_example
from tests.support.dashboard.compaction_sources import (
    chain_example,
    persist_memory,
    proof_example,
)
from tests.support.dashboard.observation import capture_read_only


async def test_exact_compaction_sources_use_immutable_rows(
    database: Path, tmp_path: Path
) -> None:
    proof = VerifiedEvidence(
        claim="first evidence",
        excerpt="first evidence",
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    await persist_memory(database, run, facts)
    before = capture_read_only(database, tmp_path / "artifacts")
    page = read_run_page(
        database,
        boundary.run_id,
        EventFilters(),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    loaded = read_compaction_sources(
        database, page.boundary, run=page.run, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert set(loaded.snapshots) == {1, 2, 3, 4}
    assert (
        project_memory(page.run, page.boundary, loaded).evidence[0].state == "verified"
    )
    assert [
        group.first_requested_sequence
        for group in project_memory(page.run, page.boundary, loaded).groups
    ] == [20, 80]
    assert capture_read_only(database, tmp_path / "artifacts") == before


async def test_coerced_event_field_cannot_be_claimed_as_original_bool(
    database: Path,
) -> None:
    event_id = UUID(int=9999)
    proof = VerifiedEvidence(
        claim="true",
        field_path=("passed",),
        value=True,
        source=EventSource(kind="event", run_id=UUID(int=1), event_id=event_id),
    )
    run, boundary, facts = proof_example(proof)
    await persist_memory(database, run, facts)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,step_number) VALUES(?,10,?,3,'evaluation_completed','2026-10-19T00:00:00Z',?,'[]',1)",
            (
                str(boundary.run_id),
                str(event_id),
                json.dumps({"type": "evaluation_completed", "passed": "true"}),
            ),
        )
        connection.commit()
    loaded = read_compaction_sources(
        database, boundary, run=run, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert project_memory(run, boundary, loaded).evidence[0].state != "verified"


async def test_unstored_snapshot_default_cannot_support_a_field_proof(
    database: Path,
) -> None:
    proof = VerifiedEvidence(
        claim="[]",
        field_path=("0", "tool_calls"),
        value=[],
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    await persist_memory(database, run, facts)
    with closing(sqlite3.connect(database)) as connection:
        # This fixture changes only the persisted optional field's presence.
        connection.execute("DROP TRIGGER run_snapshots_no_update")
        connection.execute(
            "UPDATE run_snapshots SET snapshot_json=json_remove(snapshot_json,'$.state.conversation.messages[2].tool_calls') WHERE version=1"
        )
        connection.commit()
    loaded = read_compaction_sources(
        database, boundary, run=run, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert project_memory(run, boundary, loaded).evidence[0].state != "verified"


@pytest.mark.parametrize(("count",), [(30,), (31,), (32,), (33,), (34,)])
async def test_compaction_source_chain_is_bounded(database: Path, count: int) -> None:
    run, boundary, facts = chain_example(count)
    await persist_memory(database, run, facts)
    loaded = read_compaction_sources(
        database, boundary, run=run, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    completed = [item for item in loaded.events if item.type == "compaction_completed"]
    assert len(completed) == min(count, 32)
    assert bool(loaded.unloaded_versions) == (count > 32)
    memory = project_memory(run, boundary, loaded)
    assert not any(issue.code not in ("not_loaded",) for issue in memory.issues)
    assert ("not_loaded" in {issue.code for issue in memory.issues}) == (count > 32)


async def test_history_lookup_does_not_read_current_run_body(database: Path) -> None:
    run, boundary, facts = chain_example(33)
    await persist_memory(database, run, facts)
    statements = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    loaded = read_compaction_sources(
        database,
        boundary,
        version=1,
        budget=ReadBudget(5, monotonic=lambda: 0),
        connect=connect,
    )
    assert set(loaded.snapshots) == {1, 2}
    assert not any("state_json" in statement for statement in statements)
    assert loaded.unloaded_versions == ()


async def test_visible_events_cannot_extend_the_source_boundary(database: Path) -> None:
    run, boundary, facts = memory_example()
    await persist_memory(database, run, facts)
    future = replace(
        facts.events[-1], event_id=UUID(int=9999), sequence=101, event=None
    )
    loaded = read_compaction_sources(
        database,
        boundary,
        run=run,
        visible_events=(future,),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert all(
        item.run_id == boundary.run_id and item.sequence <= boundary.as_of_sequence
        for item in loaded.events
    )
