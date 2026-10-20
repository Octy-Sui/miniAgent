import json
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from uuid import UUID, uuid4

from jinja2 import Environment

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.context import VerifiedEvidence
from tests.support.dashboard.builders import memory_example


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


import sqlite3
from contextlib import closing
from pathlib import Path

from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.storage import SQLiteRunStore


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
            request=ModelRequest(model="test", messages=()),
            attempt_id=uuid4(),
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
