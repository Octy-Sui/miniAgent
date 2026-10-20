import sqlite3
from uuid import uuid4

from miniagent.cli.resume import inspect_resume
from miniagent.runtime.resume import ResumeRejection


async def test_tc_u15_08(resume_case, monkeypatch):
    from miniagent.cli.resume import ResumeConfig

    application, _execution, provider = resume_case
    deps = application.dependencies
    connect = sqlite3.connect
    mutated = False

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)

        def trace(sql):
            nonlocal mutated
            if mutated or "SELECT * FROM run_events" not in sql:
                return
            mutated = True
            with connect(deps.run_store.path) as writer:
                writer.execute(
                    "INSERT INTO run_events SELECT run_id, (SELECT MAX(sequence)+1 FROM run_events), ?, schema_version, type, occurred_at, payload_json, artifacts_json, step_number, span_id, parent_span_id FROM run_events WHERE sequence=1",
                    (str(uuid4()),),
                )
                writer.execute(
                    "UPDATE runs SET last_event_sequence=last_event_sequence+1 WHERE run_id=?",
                    (str(deps.run_id),),
                )

        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    result = await inspect_resume(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path, dry_run=True)
    )
    assert mutated, "concurrent write window not reached"
    assert isinstance(result, ResumeRejection)
    assert result.code == "RESUME_VIEW_STALE"
    assert provider.requests == []


async def test_old_unknown_diagnostic_chain_blocks_new_commands(resume_case):
    from time import monotonic

    from miniagent.cli.diagnostic_finalization import finalize_diagnostic
    from miniagent.environments.managed_execution import HostProcessIdentity
    from miniagent.runtime.events import (
        DiagnosticFinalizationExecutionIdentified,
        DiagnosticFinalizationStarted,
        NewRunEvent,
        RunStarted,
    )
    from miniagent.runtime.recording import RunRecorder

    application, _execution, provider = resume_case
    deps = application.dependencies
    attempt = uuid4()
    identity = HostProcessIdentity(
        role="local_command",
        attempt_id=attempt,
        execution_id=uuid4(),
        pid=123,
        create_time=1,
        host_boot_id="unknown-host",
        pgid=123,
        sid=123,
    )
    payloads = [
        DiagnosticFinalizationStarted(
            attempt_id=attempt, submission_id=uuid4(), temporary_index_path="unused"
        ),
        DiagnosticFinalizationExecutionIdentified(
            attempt_id=attempt, identity=identity
        ),
        *[RunStarted() for _ in range(33)],
    ]
    for payload in payloads:
        record = await deps.run_store.get(deps.run_id)
        await deps.run_store.append(
            NewRunEvent(run_id=deps.run_id, payload=payload),
            expected_sequence=record.last_event_sequence,
        )
    record = await deps.run_store.get(deps.run_id)
    assert record.state.execution.active_operation.attempt_id != attempt
    before = sorted(deps.artifact_store.root.rglob("*"))
    result = await finalize_diagnostic(
        recorder=RunRecorder.attach(deps.run_store, record),
        workspace=deps.workspace,
        artifact_store=deps.artifact_store,
        deadline=monotonic() + 10,
    )
    assert result.error is not None
    assert result.error.code == "DIAGNOSTIC_EXECUTION_UNCONFIRMED"
    assert sorted(deps.artifact_store.root.rglob("*")) == before
    assert provider.requests == []
