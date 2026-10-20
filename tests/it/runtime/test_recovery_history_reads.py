import sqlite3
from pathlib import Path

import pytest

from miniagent.runtime import resume
from miniagent.runtime.events import RunEventType
from miniagent.runtime.store_contracts import CorruptStoredDataError
from tests.support.runtime.request_resume import started
from tests.support.storage.sqlite import make_event


async def test_sync_history_accepts_single_pass_iterable(tmp_path: Path) -> None:
    store, execution = await started(tmp_path)
    record = await store.get(execution.state.identity.run_id)
    events = await store.list_events(record.state.identity.run_id)
    assert resume.history_facts(record, iter(events)).history_errors == ()


@pytest.mark.parametrize("gap", [None, 16, 17])
async def test_tc_u15_01(tmp_path: Path, gap: int | None) -> None:
    store, execution = await started(tmp_path)
    run_id = execution.state.identity.run_id
    sequence = (await store.get(run_id)).last_event_sequence
    while sequence < 33:
        await store.append(
            make_event(run_id=run_id, type=RunEventType.RUN_STARTED),
            expected_sequence=sequence,
        )
        sequence += 1
    record = await store.get(run_id)
    if gap is not None:
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER run_events_no_delete")
            connection.execute("DELETE FROM run_events WHERE sequence=?", (gap,))
    collect = getattr(resume, "collect_history_facts", None)
    assert collect is not None, "bounded history collection is required"
    if gap is None:
        assert (await collect(record, store)).history_errors == ()
    else:
        with pytest.raises(CorruptStoredDataError, match="sequence"):
            await collect(record, store)


@pytest.mark.parametrize("scope", ["business", "diagnostic"])
@pytest.mark.parametrize("mismatched_end", [False, True])
async def test_tc_u15_05_and_06(
    tmp_path: Path, scope: str, mismatched_end: bool
) -> None:
    from uuid import uuid4

    from miniagent.environments.managed_execution import (
        ExecutionEvidence,
        HostProcessIdentity,
    )
    from miniagent.runtime.events import (
        DiagnosticFinalizationExecutionIdentified,
        DiagnosticFinalizationStarted,
        NewRunEvent,
        OperationExecutionEnded,
        OperationExecutionIdentified,
    )

    store, execution = await started(tmp_path)
    run_id = execution.state.identity.run_id
    attempt_id = uuid4()
    identity = HostProcessIdentity(
        role="local_command",
        attempt_id=attempt_id,
        execution_id=uuid4(),
        pid=123,
        create_time=1,
        host_boot_id="boot",
        pgid=123,
        sid=123,
    )
    kind = (
        DiagnosticFinalizationExecutionIdentified
        if scope == "diagnostic"
        else OperationExecutionIdentified
    )
    if scope == "diagnostic":
        record = await store.get(run_id)
        await store.append(
            NewRunEvent(
                run_id=run_id,
                payload=DiagnosticFinalizationStarted(
                    attempt_id=attempt_id,
                    submission_id=uuid4(),
                    temporary_index_path="unused",
                ),
            ),
            expected_sequence=record.last_event_sequence,
        )
    payload = kind(attempt_id=attempt_id, identity=identity)
    record = await store.get(run_id)
    await store.append(
        NewRunEvent(run_id=run_id, payload=payload),
        expected_sequence=record.last_event_sequence,
    )
    if mismatched_end:
        different = identity.model_copy(update={"pid": 456})
        evidence = ExecutionEvidence(
            execution_id=identity.execution_id,
            identity=different,
            outcome="ended",
            started_observed=True,
            group_ended=True,
            issuer_ended=True,
        )
        record = await store.get(run_id)
        await store.append(
            NewRunEvent(
                run_id=run_id,
                payload=OperationExecutionEnded(
                    attempt_id=attempt_id, evidence=evidence
                ),
            ),
            expected_sequence=record.last_event_sequence,
        )
    record = await store.get(run_id)
    for sequence in range(record.last_event_sequence, 35):
        await store.append(
            make_event(run_id=run_id, type=RunEventType.RUN_STARTED),
            expected_sequence=sequence,
        )
    facts = await resume.collect_history_facts(await store.get(run_id), store)
    chain = next(
        item for item in facts.execution_chains if item.attempt_id == attempt_id
    )
    assert chain.identities == (identity,)
    assert chain.scope == scope
    if mismatched_end:
        assert "Execution end has no matching identified fact" in facts.history_errors
    else:
        assert chain.evidence == ()
        assert attempt_id not in facts.interrupted_attempt_ids


@pytest.mark.parametrize(
    "variant", ["duplicate_resume_start", "compaction_selection_conflict"]
)
async def test_tc_u15_07(tmp_path: Path, variant: str, runtime_persistence) -> None:
    import json

    from miniagent.runtime import AgentRuntime
    from miniagent.runtime.events import CompactionRequested, ResumeStarted
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.compaction_resume import failed_summary
    from tests.support.runtime.runtime import QueueProvider
    from tests.support.runtime.runtime_resume import completed_protocol

    if variant == "duplicate_resume_start":
        store, execution = await started(tmp_path)
        record = await completed_protocol(store, execution)
        events = await store.list_events(record.state.identity.run_id)
        start = next(
            event for event in events if isinstance(event.payload, ResumeStarted)
        )
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute(
                "UPDATE run_events SET type='resume_started',payload_json=? WHERE sequence=1",
                (start.payload.model_dump_json(),),
            )
        provider = QueueProvider([])
        runtime = AgentRuntime(
            artifact_store=runtime_persistence.artifacts,
            provider=provider,
            tools=ToolRegistry([]),
            run_store=store,
        )
        with pytest.raises(ValueError, match="unique start"):
            await runtime.resume(record)
        assert provider.requests == []
    else:
        runtime, execution, store = await failed_summary(tmp_path, 1)
        events = await store.list_events(execution.state.identity.run_id)
        requested = next(
            event for event in events if isinstance(event.payload, CompactionRequested)
        )
        payload = json.loads(requested.payload.model_dump_json())
        payload["hard_trigger"] = not payload["hard_trigger"]
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute(
                "UPDATE run_events SET payload_json=? WHERE event_id=?",
                (json.dumps(payload), str(requested.event_id)),
            )
        with pytest.raises(ValueError, match="inconsistent"):
            await runtime._prepare_request(execution, force=True)
        assert runtime.provider.requests == []
