import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.runtime.context import (
    InvalidSummaryError,
    build_summary_request,
    validate_compaction_record,
    validate_summary,
)
from miniagent.runtime.events import CompactionCompleted, RunEventType
from miniagent.storage import SQLiteRunStore
from tests.support.runtime.compaction_resume import failed_summary
from tests.support.runtime.context import summary_plan, summary_response
from tests.support.runtime.runtime import QueueProvider
from tests.support.storage.sqlite import make_event


async def published(tmp_path: Path):
    runtime, execution, store = await failed_summary(tmp_path, 1)
    runtime.provider = QueueProvider([summary_response()])
    await runtime._prepare_request(execution, force=True)
    record = await store.get(execution.state.identity.run_id)
    events = await store.list_events(record.state.identity.run_id)
    event = next(
        event for event in events if isinstance(event.payload, CompactionCompleted)
    )
    return store, record, event


@pytest.mark.parametrize("variant", ["duplicate_version", "broken_chain", "cycle"])
async def test_tc_u15_02(tmp_path: Path, variant: str) -> None:
    store, record, event = await published(tmp_path)
    await validate_compaction_record(record, store)
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        payload = json.loads(event.payload.model_dump_json())
        if variant == "duplicate_version":
            target = event.sequence - 1
        else:
            target = event.sequence
            payload["previous_version"] = 99 if variant == "broken_chain" else 1
        connection.execute(
            "UPDATE run_events SET payload_json=?, type='compaction_completed' WHERE sequence=?",
            (json.dumps(payload), target),
        )
    with pytest.raises(ValueError, match="compaction"):
        await validate_compaction_record(record, store)


@pytest.mark.parametrize(
    "variant", ["missing_snapshot", "hash", "version", "retained_messages"]
)
async def test_tc_u15_04(tmp_path: Path, variant: str) -> None:
    store, record, event = await published(tmp_path)
    with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        payload = json.loads(event.payload.model_dump_json())
        if variant == "missing_snapshot":
            payload["source_snapshot_version"] = 999
        elif variant == "hash":
            payload["summary_sha256"] = "0" * 64
        elif variant == "version":
            payload["new_snapshot_version"] = payload["source_snapshot_version"]
        else:
            payload["covered_messages"] = [2, 3]
        connection.execute(
            "UPDATE run_events SET payload_json=? WHERE event_id=?",
            (json.dumps(payload), str(event.event_id)),
        )
    with pytest.raises(ValueError, match="compaction"):
        await validate_compaction_record(record, store)


@pytest.mark.parametrize(
    "variant",
    ["valid", "wrong_run", "beyond_boundary", "wrong_step", "field", "excerpt"],
)
async def test_tc_u15_03(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    path = tmp_path / "runs.db"
    store = SQLiteRunStore(path)
    await store.initialize()
    plan = replace(summary_plan(), source_event_sequence=17)
    run_id = plan.state.identity.run_id
    await store.create(plan.state)
    events = []
    for number in range(18):
        events.append(
            await store.append(
                make_event(
                    run_id=run_id,
                    type=RunEventType.MODEL_REQUESTED,
                    step_number=1 if number != 16 else 6,
                    span_id="1111111111111111",
                ),
                expected_sequence=number,
            )
        )
    target = events[0]
    if variant == "beyond_boundary":
        target = events[17]
    elif variant == "wrong_step":
        target = events[16]
    evidence = {
        "claim": '"test"',
        "field_path": ["provider"],
        "value": "test",
        "source": {
            "kind": "event",
            "run_id": str(uuid4() if variant == "wrong_run" else run_id),
            "event_id": str(target.event_id),
        },
    }
    if variant == "field":
        evidence["value"] = "false"
    elif variant == "excerpt":
        evidence = {
            "claim": "absent",
            "excerpt": "absent",
            "source": evidence["source"],
        }
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    if variant == "valid":
        await validate_summary(
            summary_response(verified_evidence=[evidence] * 3), plan, store
        )
        queries = [sql for sql in statements if "FROM run_events" in sql]
        assert len([sql for sql in queries if "event_id =" in sql]) == 3
        assert all("sequence <= 17" in sql for sql in queries)
    else:
        with pytest.raises(InvalidSummaryError, match="source"):
            await validate_summary(
                summary_response(verified_evidence=[evidence]), plan, store
            )


async def test_multiple_versions_use_filtered_metadata_and_point_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.runtime.context import select_compaction_prefix
    from miniagent.runtime.events import CompactionAttempt
    from tests.support.runtime.context import SizedEstimator

    runtime, execution, store = await failed_summary(tmp_path, 0)
    runtime.estimator = SizedEstimator(1000)
    for _ in range(2):
        state = execution.state
        plan = select_compaction_prefix(
            state,
            runtime._build_request(state),
            runtime.estimator,
            force=True,
            covered_messages=(2, state.conversation.completed_steps[0].message_end),
        )
        source = await store.load_latest(state.identity.run_id)
        assert plan is not None and source is not None
        plan = replace(
            plan,
            source_snapshot_version=source.version,
            source_event_sequence=source.event_sequence,
        )
        conversation = await validate_summary(summary_response(), plan, store)
        await execution.begin_compaction_attempt(
            plan,
            1,
            branch="forced_compaction",
            request=build_summary_request(plan.state, plan),
        )
        await execution.record_compaction_attempt(CompactionAttempt(attempt=1))
        await execution.publish_compaction(
            conversation,
            plan,
            attempts=(CompactionAttempt(attempt=1),),
            new_tokens=1000,
            summary_input_tokens=1000,
            source_snapshot_id=source.snapshot_id,
        )
    record = await store.get(execution.state.identity.run_id)
    assert record.state.context.current_version == 2
    statements: list[str] = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    await validate_compaction_record(record, store)
    event_queries = [sql for sql in statements if "SELECT * FROM run_events" in sql]
    assert event_queries
    assert all(
        "type IN ('compaction_completed')" in sql and "LIMIT 16" in sql
        for sql in event_queries
    )
    assert all(
        f"sequence <= {record.last_event_sequence}" in sql for sql in event_queries
    )
