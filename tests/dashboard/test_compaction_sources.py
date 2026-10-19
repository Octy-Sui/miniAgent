import json
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from uuid import UUID

import pytest
from jinja2 import Environment

from miniagent.dashboard.projections.compaction import project_memory
from miniagent.dashboard.reads import ReadIssue
from miniagent.models.messages import AssistantMessage
from miniagent.runtime.context import SnapshotSource, VerifiedEvidence
from tests.dashboard.builders import memory_example


def proof_example(proof: VerifiedEvidence):
    run, boundary, facts = memory_example()
    assert run.record is not None
    state = run.record.state
    content = state.conversation.messages[2].content
    assert content is not None
    data = json.loads(content.split("\n", 1)[1])
    data["verified_evidence"] = [proof.model_dump(mode="json")]
    template = Environment(autoescape=False, keep_trailing_newline=True).from_string(
        files("miniagent.runtime").joinpath("templates/compaction_v1.j2").read_text()
    )
    rendered = template.render(summary_json=json.dumps(data))
    conversation = state.conversation.model_copy(
        update={
            "messages": (
                *state.conversation.messages[:2],
                AssistantMessage(content=rendered),
                *state.conversation.messages[3:],
            )
        }
    )
    state = state.model_copy(update={"conversation": conversation})
    run = replace(run, record=run.record.model_copy(update={"state": state}))
    snapshots = dict(facts.snapshots)
    carrier = snapshots[4]
    assert carrier.snapshot is not None
    snapshots[4] = replace(
        carrier, snapshot=carrier.snapshot.model_copy(update={"state": state})
    )
    events = []
    for item in facts.events:
        if item.sequence == 100:
            assert item.event is not None
            item = replace(
                item,
                event=item.event.model_copy(
                    update={
                        "payload": item.event.payload.model_copy(
                            update={
                                "summary_sha256": sha256(rendered.encode()).hexdigest()
                            }
                        )
                    }
                ),
            )
        events.append(item)
    return run, boundary, replace(facts, snapshots=snapshots, events=tuple(events))


@pytest.mark.parametrize(
    ("claim", "excerpt", "expected"),
    [
        ("first evidence", "first evidence", "verified"),
        ("invented conclusion", "first evidence", "inconsistent"),
        ("content", "content", "inconsistent"),
        ("absent leaf", "absent leaf", "inconsistent"),
    ],
)
def test_existing_source_must_support_claim(
    claim: str, excerpt: str, expected: str
) -> None:
    proof = VerifiedEvidence(
        claim=claim,
        excerpt=excerpt,
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    memory = project_memory(run, boundary, facts)
    assert memory.evidence[0].state == expected
    assert all(field.source_label == "摘要记录" for field in memory.fields)


def test_unloaded_evidence_is_not_missing() -> None:
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
    snapshots = dict(facts.snapshots)
    snapshots[1] = replace(
        snapshots[1], snapshot=None, issues=(ReadIssue("read_limit_exceeded", "超限"),)
    )
    memory = project_memory(
        run, boundary, replace(facts, snapshots=snapshots, unloaded_versions=(1,))
    )
    assert memory.evidence[0].state == "not_loaded"
    assert "source_missing" not in {issue.code for issue in memory.evidence[0].issues}


@pytest.mark.parametrize(
    ("change",),
    [
        ({"run_id": UUID(int=99)},),
        ({"snapshot_version": 2},),
        ({"message_start": 3, "message_end": 4},),
    ],
)
def test_source_identity_and_ancestor_range_are_required(change: dict) -> None:
    source = SnapshotSource.model_validate(
        {
            "kind": "snapshot",
            "run_id": UUID(int=1),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 3,
        }
        | change
    )
    run, boundary, facts = proof_example(
        VerifiedEvidence(
            claim="first evidence", excerpt="first evidence", source=source
        )
    )
    assert project_memory(run, boundary, facts).evidence[0].state == "inconsistent"


import sqlite3
from contextlib import closing
from pathlib import Path

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.events import read_compaction_sources
from miniagent.dashboard.queries.runs import read_run_page
from miniagent.dashboard.reads import ReadBudget
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.storage import SQLiteRunStore
from tests.dashboard.observation import capture_read_only


async def persist_memory(database: Path, run, facts) -> None:
    assert run.record is not None
    await SQLiteRunStore(database).create(run.record.state)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET last_event_sequence=?,latest_snapshot_version=?",
            (run.envelope.last_event_sequence, run.envelope.latest_snapshot_version),
        )
        for item in facts.events:
            assert item.event is not None
            connection.execute(
                "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,step_number,span_id,parent_span_id) VALUES(?,?,?,3,?,?,?,'[]',?,?,?)",
                (
                    str(item.run_id),
                    item.sequence,
                    str(item.event_id),
                    item.type,
                    item.occurred_at.isoformat(),
                    item.event.payload.model_dump_json(),
                    item.step_number,
                    item.span_id,
                    item.parent_span_id,
                ),
            )
        for stored in facts.snapshots.values():
            assert stored.snapshot is not None
            snapshot = stored.snapshot
            envelope = NewRunSnapshot(
                run_id=stored.run_id,
                event_sequence=stored.event_sequence,
                state=snapshot.state,
                recovery_context=snapshot.recovery_context,
            )
            connection.execute(
                "INSERT INTO run_snapshots(run_id,version,snapshot_id,event_sequence,schema_version,snapshot_json,created_at) VALUES(?,?,?,?,3,?,?)",
                (
                    str(stored.run_id),
                    stored.version,
                    str(stored.snapshot_id),
                    stored.event_sequence,
                    envelope.model_dump_json(),
                    snapshot.created_at.isoformat(),
                ),
            )
        connection.commit()


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


from miniagent.runtime.context import EventSource


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


from miniagent.dashboard.reads import CompactionRead, StoredEvent, StoredSnapshot
from miniagent.runtime.events import CompactionCompleted, CompactionRequested, RunEvent
from miniagent.runtime.snapshots import RunSnapshot
from miniagent.runtime.state import ContextProgress, ConversationState, StepRange


def chain_example(count: int):
    run, boundary, original = memory_example()
    assert run.record is not None
    base = run.record.state
    template_event = next(
        item.event for item in original.events if item.sequence == 100
    )
    assert template_event is not None
    assert isinstance(template_event.payload, CompactionCompleted)
    messages = base.conversation.messages[:2]
    template = Environment(autoescape=False, keep_trailing_newline=True).from_string(
        files("miniagent.runtime").joinpath("templates/compaction_v1.j2").read_text()
    )
    snapshots = {}
    events = []
    previous = ""
    state = base
    for n in range(1, count + 1):
        summary = {
            "current_goal": f"goal {n}",
            "user_constraints": [],
            "verified_evidence": [],
            "workspace_changes": [],
            "rejected_attempts": [],
            "uncertainties": [],
            "open_items": ["finish"],
            "next_action": "finish",
        }
        rendered = template.render(summary_json=json.dumps(summary))
        old_messages = (
            (
                *messages,
                AssistantMessage(content="first evidence" if n == 1 else previous),
                AssistantMessage(content=f"retained {n + 1}"),
            )
            if n == 1
            else (
                *messages,
                AssistantMessage(content=previous),
                AssistantMessage(content=f"retained {n}"),
                AssistantMessage(content=f"retained {n + 1}"),
            )
        )
        start = 2 if n == 1 else 3
        end = 3 if n == 1 else 4
        old = base.model_copy(
            update={
                "context": ContextProgress(current_version=n - 1 if n > 1 else None),
                "conversation": ConversationState(
                    messages=old_messages,
                    completed_steps=(
                        StepRange(step_number=n, message_start=start, message_end=end),
                        StepRange(
                            step_number=n + 1, message_start=end, message_end=end + 1
                        ),
                    ),
                ),
            }
        )
        state = base.model_copy(
            update={
                "context": ContextProgress(current_version=n),
                "conversation": ConversationState(
                    messages=(
                        *messages,
                        AssistantMessage(content=rendered),
                        AssistantMessage(content=f"retained {n + 1}"),
                    ),
                    completed_steps=(
                        StepRange(step_number=n + 1, message_start=3, message_end=4),
                    ),
                ),
            }
        )
        for version, snapshot_state, sequence in (
            (2 * n - 1, old, 10 * n - 2),
            (2 * n, state, 10 * n),
        ):
            snapshot = RunSnapshot(
                run_id=boundary.run_id,
                event_sequence=sequence,
                state=snapshot_state,
                snapshot_id=UUID(int=20000 + version),
                version=version,
                created_at=boundary.read_at,
            )
            snapshots[version] = StoredSnapshot(
                snapshot.snapshot_id, boundary.run_id, version, sequence, snapshot, ()
            )
        request = CompactionRequested(
            provider="test",
            model="test",
            attempt=1,
            source_snapshot_version=2 * n - 1,
            covered_messages=(2, end),
        )
        completed = template_event.payload.model_copy(
            update={
                "previous_version": n - 1 if n > 1 else None,
                "current_version": n,
                "source_snapshot_version": 2 * n - 1,
                "source_snapshot_id": UUID(int=20000 + 2 * n - 1),
                "new_snapshot_version": 2 * n,
                "new_snapshot_id": UUID(int=20000 + 2 * n),
                "source_event_sequence": 10 * n - 2,
                "event_sequence": 10 * n,
                "covered_messages": (2, end),
                "covered_steps": (n,),
                "retained_first_step": n + 1,
                "summary_sha256": sha256(rendered.encode()).hexdigest(),
                "attempts": (),
            }
        )
        for payload, sequence in ((request, 10 * n - 1), (completed, 10 * n)):
            event = RunEvent(
                run_id=boundary.run_id,
                event_id=UUID(int=100000 + sequence),
                payload=payload,
                sequence=sequence,
                occurred_at=boundary.read_at,
            )
            events.append(
                StoredEvent(
                    event.event_id,
                    boundary.run_id,
                    sequence,
                    str(event.type),
                    event.occurred_at,
                    None,
                    None,
                    None,
                    event,
                    (),
                )
            )
        previous = rendered
    envelope = replace(
        run.envelope, last_event_sequence=count * 10, latest_snapshot_version=count * 2
    )
    record = run.record.model_copy(
        update={
            "state": state,
            "last_event_sequence": count * 10,
            "latest_snapshot_version": count * 2,
        }
    )
    return (
        replace(run, envelope=envelope, record=record),
        boundary.model_copy(
            update={"as_of_sequence": count * 10, "latest_snapshot_version": count * 2}
        ),
        CompactionRead(tuple(events), snapshots, {}, (), (), ()),
    )


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


@pytest.mark.parametrize(
    ("value", "claim", "expected"),
    [
        ("first evidence", '"first evidence"', "verified"),
        (True, "true", "inconsistent"),
        (1, "1", "inconsistent"),
        ("first evidence", "first evidence", "inconsistent"),
    ],
)
def test_field_proof_checks_actual_type_and_canonical_claim(
    value, claim: str, expected: str
) -> None:
    proof = VerifiedEvidence(
        claim=claim,
        field_path=("0", "content"),
        value=value,
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    assert project_memory(run, boundary, facts).evidence[0].state == expected


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
