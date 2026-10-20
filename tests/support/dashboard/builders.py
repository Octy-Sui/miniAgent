from pathlib import Path

from miniagent.storage import SQLiteRunStore


async def make_database(path: Path) -> Path:
    store = SQLiteRunStore(path)
    await store.initialize()
    return path


from datetime import UTC, datetime
from uuid import UUID

from miniagent.agents import AgentSpec
from miniagent.models.config import ModelTarget
from miniagent.runtime import RuntimeConfig, create_run_state


async def make_run(
    database: Path,
    number: int,
    *,
    task: str = "visible task",
    created_at: datetime | None = None,
) -> UUID:
    run_id = UUID(int=number)
    state = create_run_state(
        run_id=run_id,
        task=task,
        agent_spec=AgentSpec(
            name="dashboard-test",
            system_prompt="test",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=created_at or datetime(2026, 10, 19, tzinfo=UTC),
    )
    await SQLiteRunStore(database).create(state)
    return run_id


from decimal import Decimal
from hashlib import sha256
from importlib.resources import files

from jinja2 import Environment, StrictUndefined

from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.reads import (
    CompactionRead,
    RunEnvelope,
    StoredEvent,
    StoredRun,
    StoredSnapshot,
)
from miniagent.metering.usage import RequestCost
from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.runtime.context import CompactionSummary
from miniagent.runtime.events import (
    CompactionAttempt,
    CompactionAttemptRecorded,
    CompactionCompleted,
    CompactionRequested,
    EventPayload,
    RunEvent,
)
from miniagent.runtime.snapshots import RunSnapshot
from miniagent.runtime.state import ContextProgress, ConversationState, StepRange
from miniagent.runtime.store_contracts import RunRecord


def memory_example(
    *, display_text: str | None = None
) -> tuple[StoredRun, PageBoundary, CompactionRead]:
    run_id = UUID(int=1)
    now = datetime(2026, 10, 19, tzinfo=UTC)
    base = create_run_state(
        run_id=run_id,
        task="task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="fixed",
            model=ModelTarget(provider="test", model="test"),
        ),
        config=RuntimeConfig(),
        created_at=now,
    )
    template = Environment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    ).from_string(
        files("miniagent.runtime").joinpath("templates/compaction_v1.j2").read_text()
    )
    summaries = [
        CompactionSummary(
            current_goal=display_text
            if n == 2 and display_text is not None
            else f"goal {n}",
            user_constraints=["visible constraint"],
            verified_evidence=[],
            workspace_changes=["change"],
            rejected_attempts=[],
            uncertainties=["uncertain"],
            open_items=["finish"],
            next_action="finish",
        )
        for n in (1, 2)
    ]
    rendered = [
        template.render(summary_json=summary.model_dump_json()) for summary in summaries
    ]
    prefix = (SystemMessage(content="fixed"), UserMessage(content="task"))
    first = AssistantMessage(content="first evidence")
    retained = AssistantMessage(content="retained evidence")
    last = AssistantMessage(content="last retained")
    conversations = [
        ConversationState(
            messages=(*prefix, first, retained),
            completed_steps=(
                StepRange(step_number=1, message_start=2, message_end=3),
                StepRange(step_number=2, message_start=3, message_end=4),
            ),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[0]), retained),
            completed_steps=(StepRange(step_number=2, message_start=3, message_end=4),),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[0]), retained, last),
            completed_steps=(
                StepRange(step_number=2, message_start=3, message_end=4),
                StepRange(step_number=3, message_start=4, message_end=5),
            ),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[1]), last),
            completed_steps=(StepRange(step_number=3, message_start=3, message_end=4),),
        ),
    ]
    states = [
        base.model_copy(
            update={
                "conversation": conversation,
                "context": ContextProgress(current_version=version),
            }
        )
        for conversation, version in zip(conversations, (None, 1, 1, 2), strict=True)
    ]
    snapshots = {
        n: StoredSnapshot(
            UUID(int=100 + n),
            run_id,
            n,
            sequence,
            RunSnapshot(
                run_id=run_id,
                event_sequence=sequence,
                state=state,
                snapshot_id=UUID(int=100 + n),
                version=n,
                created_at=now,
            ),
            (),
        )
        for n, (state, sequence) in enumerate(
            zip(states, (20, 40, 80, 100), strict=True), 1
        )
    }

    def stored(payload: EventPayload, sequence: int) -> StoredEvent:
        event = RunEvent(
            run_id=run_id,
            event_id=UUID(int=1000 + sequence),
            payload=payload,
            sequence=sequence,
            occurred_at=now,
        )
        return StoredEvent(
            event.event_id,
            run_id,
            sequence,
            str(event.type),
            now,
            None,
            None,
            None,
            event,
            (),
        )

    def attempt(n: int, cost: str) -> CompactionAttempt:
        price = Decimal(cost)
        return CompactionAttempt(
            attempt=n,
            request_cost=RequestCost(
                provider="test",
                model="test",
                currency="USD",
                input_cost=price,
                total_cost=price,
            ),
        )

    a, b, c = attempt(1, "0.10"), attempt(2, "0.20"), attempt(1, "0.40")
    events = []
    for version, sequence, source, new, covered, steps, attempts in [
        (1, 40, 1, 2, (2, 3), (1,), (a, b)),
        (2, 100, 3, 4, (2, 4), (2,), (c,)),
    ]:
        start = 20 if version == 1 else 80
        events.append(
            stored(
                CompactionRequested(
                    provider="test",
                    model="test",
                    attempt=1,
                    source_snapshot_version=source,
                    covered_messages=covered,
                ),
                start,
            )
        )
        for offset, item in enumerate(attempts, 1):
            events.append(
                stored(CompactionAttemptRecorded(result=item), start + offset)
            )
        payload = CompactionCompleted(
            previous_version=None if version == 1 else 1,
            current_version=version,
            source_snapshot_version=source,
            source_snapshot_id=UUID(int=100 + source),
            source_event_sequence=start,
            new_snapshot_version=new,
            new_snapshot_id=UUID(int=100 + new),
            event_sequence=sequence,
            covered_messages=covered,
            covered_steps=steps,
            retained_first_step=version + 1,
            summary_sha256=sha256(rendered[version - 1].encode()).hexdigest(),
            provider="test",
            model="test",
            context_window_tokens=32768,
            output_reserve=1000,
            safety_margin=1024,
            business_budget=30000,
            summary_reserve=2048,
            old_input_tokens=25000,
            new_input_tokens=12000,
            summary_input_tokens=300,
            hard_trigger=False,
            attempts=attempts,
        )
        events.append(stored(payload, sequence))
    record = RunRecord(
        state=states[-1], last_event_sequence=100, latest_snapshot_version=4
    )
    envelope = RunEnvelope(run_id, 3, base.lifecycle.status, now, now, None, 100, 4)
    return (
        StoredRun(envelope, record, "unknown", ()),
        PageBoundary(
            run_id=run_id,
            as_of_sequence=100,
            latest_snapshot_version=4,
            run_status="created",
            read_at=now,
        ),
        CompactionRead(tuple(events), snapshots, {}, (), (), ()),
    )


import json
from dataclasses import dataclass, replace
from datetime import timedelta

from miniagent.artifacts.contracts import ArtifactRecord
from miniagent.dashboard.reads import ManifestItem, VerifiedContent
from miniagent.environments.config import DockerOptions
from miniagent.environments.persistence import PersistedDockerWorkspaceSpec
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import EvaluationCompleted
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.result import TerminationReason
from miniagent.runtime.state import RunStatus


@dataclass(frozen=True)
class EvaluationExample:
    run: StoredRun
    boundary: PageBoundary
    events: tuple[StoredEvent, ...]
    manifests: tuple[ManifestItem, ...]
    contents: dict[UUID, VerifiedContent]


def report_document() -> dict:
    return {
        "org__repo-1": {
            "patch_exists": True,
            "patch_is_None": False,
            "patch_successfully_applied": True,
            "resolved": True,
            "infra_failure": False,
            "tests_status": {
                "FAIL_TO_PASS": {
                    "success": ["tests/test_a.py::test_one"],
                    "failure": [],
                },
                "PASS_TO_PASS": {
                    "success": ["tests/test_b.py::test_two"],
                    "failure": [],
                },
            },
        }
    }


def evaluation_example(root: Path = Path("/fixture")) -> EvaluationExample:
    root = root.resolve()
    now = datetime(2026, 10, 19, tzinfo=UTC)
    run_id, attempt, submission = UUID(int=1), UUID(int=101), UUID(int=102)
    contents = {}

    def artifact(number: int, kind: str, body: bytes, metadata: dict) -> ArtifactRecord:
        digest = sha256(body).hexdigest()
        record = ArtifactRecord(
            artifact_id=UUID(int=number),
            run_id=run_id,
            type=kind,
            name="changes.patch" if kind == "workspace_patch" else f"{kind}.json",
            media_type="application/json"
            if kind in ("evaluation_config", "evaluation_summary", "evaluation_report")
            else "text/x-diff"
            if kind == "workspace_patch"
            else "text/plain",
            uri=(root / "content" / digest[:2] / digest).as_uri(),
            size_bytes=len(body),
            sha256=digest,
            metadata=metadata,
        )
        contents[record.artifact_id] = VerifiedContent(record, body, now, ())
        return record

    patch = artifact(
        103,
        "workspace_patch",
        b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        {
            "submission_id": str(submission),
            "kind": "candidate",
            "stage": "submission",
            "files": ["a.py"],
            "base_revision": "abc123",
        },
    )
    identity = {
        "run_id": str(run_id),
        "attempt_id": str(attempt),
        "instance_id": "org__repo-1",
        "submission_id": str(submission),
        "patch_artifact_id": str(patch.artifact_id),
        "patch_sha256": patch.sha256,
    }
    metadata = {
        "attempt_id": str(attempt),
        "instance_id": "org__repo-1",
        "stage": "evaluation",
    }
    config = {
        **identity,
        "model_name": "test",
        "model_key": "model-test",
        "patch_ref": patch.reference().model_dump(mode="json"),
        "predictions_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "tasks_sha256": "c" * 64,
        "record_sha256": "d" * 64,
        "harness_version": "5.0.2",
        "default_image": "test-image",
        "actual_image": "test-image",
        "image_preflight_verified": True,
        "base_revision": "abc123",
    }
    artifact(104, "evaluation_config", json.dumps(config).encode(), metadata)
    artifact(
        105,
        "evaluation_summary",
        json.dumps(
            {
                **identity,
                "scope": "evaluation_evidence_snapshot",
                "terminal_status_source": "Run",
                "verdict": "report_resolved",
                "error": None,
                "message": "",
                "diagnostics": [],
                "unregistered_files": [],
                "containers": [],
            }
        ).encode(),
        metadata,
    )
    artifact(106, "evaluation_report", json.dumps(report_document()).encode(), metadata)
    for number, kind in enumerate(
        (
            "evaluation_stdout",
            "evaluation_stderr",
            "evaluation_test_output",
            "evaluation_instance_log",
        ),
        107,
    ):
        artifact(number, kind, b"visible recorded log\n", metadata)
    state = create_run_state(
        run_id=run_id,
        task="inspect persisted evidence",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="test",
            model=ModelTarget(provider="test", model="test"),
        ),
        config=RuntimeConfig(),
        created_at=now,
    )
    finished = now + timedelta(seconds=20)
    state = state.model_copy(
        update={
            "lifecycle": state.lifecycle.model_copy(
                update={
                    "status": RunStatus.SUCCEEDED,
                    "started_at": now,
                    "updated_at": finished,
                    "finished_at": finished,
                    "termination_reason": TerminationReason.TASK_VERIFIED,
                    "candidate_output": "candidate",
                    "final_output": "done",
                }
            ),
            "execution": state.execution.model_copy(update={"phase": None}),
        }
    )
    state = type(state).model_validate(state.model_dump())
    context = RunRecoveryContext(
        workspace_id="destroyed-workspace",
        workspace_spec=PersistedDockerWorkspaceSpec(
            run_id=run_id, options=DockerOptions(image="test-image")
        ),
    )
    record = RunRecord(
        state=state,
        last_event_sequence=5,
        latest_snapshot_version=0,
        recovery_context=context,
    )
    envelope = RunEnvelope(
        run_id, 3, RunStatus.SUCCEEDED, now, finished, finished, 5, 0
    )
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=5,
        latest_snapshot_version=0,
        run_status="succeeded",
        read_at=finished,
    )
    refs = [content.record.reference() for content in contents.values()]
    event = RunEvent(
        run_id=run_id,
        event_id=UUID(int=120),
        sequence=5,
        occurred_at=finished,
        payload=EvaluationCompleted(
            passed=True,
            result=EvaluationResult(passed=True, output="done", artifacts=refs),
        ),
        artifacts=refs,
    )
    stored = StoredEvent(
        event.event_id,
        run_id,
        5,
        str(event.type),
        finished,
        None,
        None,
        None,
        event,
        (),
    )
    return EvaluationExample(
        StoredRun(envelope, record, "docker", ()),
        boundary,
        (stored,),
        tuple(ManifestItem(key, value.record, ()) for key, value in contents.items()),
        contents,
    )


def change_evaluation_content(
    example: EvaluationExample, number: int, document: dict
) -> EvaluationExample:
    artifact_id = UUID(int=number)
    body = json.dumps(document).encode()
    original = example.contents[artifact_id]
    digest = sha256(body).hexdigest()
    content_uri = original.record.uri.rsplit("/", 2)[0] + f"/{digest[:2]}/{digest}"
    record = original.record.model_copy(
        update={"sha256": digest, "size_bytes": len(body), "uri": content_uri}
    )
    contents = {
        **example.contents,
        artifact_id: VerifiedContent(record, body, original.read_at, ()),
    }
    manifests = tuple(
        ManifestItem(item.artifact_id, record, item.issues)
        if item.artifact_id == artifact_id
        else item
        for item in example.manifests
    )
    events = []
    for item in example.events:
        assert item.event is not None and isinstance(
            item.event.payload, EvaluationCompleted
        )
        refs = [
            record.reference() if ref.artifact_id == artifact_id else ref
            for ref in item.event.artifacts
        ]
        result = item.event.payload.result
        if result is not None:
            result = result.model_copy(update={"artifacts": refs})
        event = item.event.model_copy(
            update={
                "artifacts": refs,
                "payload": item.event.payload.model_copy(update={"result": result}),
            }
        )
        events.append(replace(item, event=event))
    return replace(
        example, contents=contents, manifests=manifests, events=tuple(events)
    )


async def persist_memory(database: Path, *, display_text: str | None = None) -> None:
    import sqlite3
    from contextlib import closing

    from miniagent.storage.codecs import encode_datetime

    run, boundary, facts = memory_example(display_text=display_text)
    assert run.record is not None
    await SQLiteRunStore(database).create(run.record.state)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET last_event_sequence=?,latest_snapshot_version=? WHERE run_id=?",
            (
                boundary.as_of_sequence,
                boundary.latest_snapshot_version,
                str(boundary.run_id),
            ),
        )
        for item in facts.events:
            event = item.event
            assert event is not None
            connection.execute(
                "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,step_number,span_id,parent_span_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(event.run_id),
                    event.sequence,
                    str(event.event_id),
                    event.schema_version,
                    str(event.type),
                    encode_datetime(event.occurred_at),
                    event.payload.model_dump_json(),
                    json.dumps(
                        [ref.model_dump(mode="json") for ref in event.artifacts]
                    ),
                    event.step_number,
                    event.span_id,
                    event.parent_span_id,
                ),
            )
        for item in facts.snapshots.values():
            snapshot = item.snapshot
            assert snapshot is not None
            connection.execute(
                "INSERT INTO run_snapshots(run_id,version,snapshot_id,event_sequence,schema_version,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    str(snapshot.run_id),
                    snapshot.version,
                    str(snapshot.snapshot_id),
                    snapshot.event_sequence,
                    snapshot.schema_version,
                    snapshot.model_dump_json(
                        exclude={"snapshot_id", "version", "created_at"}
                    ),
                    encode_datetime(snapshot.created_at),
                ),
            )
        connection.commit()
