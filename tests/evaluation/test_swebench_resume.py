import asyncio
import json
import sys
from multiprocessing import Pipe
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.environments.process import identify_host_process, reap_process
from miniagent.evaluation.swebench_docker import (
    EvaluationAttemptBusy,
    SWEbenchAttemptState,
    cleanup_attempt_resources,
    inspect_attempt_resources,
)
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.runtime import RuntimeConfig, create_run_state
from miniagent.runtime.events import RunEventType
from miniagent.runtime.execution import RunExecution, evaluation_scope
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import CandidateDisposition
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.storage import SQLiteRunStore
from tests.resume_support import StoreBoundary
from tests.runtime.test_runtime import agent, final_response


async def scope_for(evaluator, request, tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    spec = agent()
    state = create_run_state(
        run_id=request.run_id,
        task=request.task,
        agent_spec=spec,
        config=RuntimeConfig(),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content=request.task),
        ),
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(run_id=request.run_id, store=store, recovery_context=None)
        ),
    )
    await execution.begin_step(provider="test", model="test-model")
    await execution.record_model_response(
        final_response(request.candidate.text),
        None,
        disposition=CandidateDisposition(text=request.candidate.text),
    )
    await execution.begin_evaluation(request.candidate.text)
    return store, execution


async def test_attempt_is_committed_before_directory_and_harness(
    evaluation_case, tmp_path
):
    """TC-U16-01 / AC11: real commit handshake precedes every attempt side effect."""
    evaluator, request, _artifacts = evaluation_case
    store, execution = await scope_for(evaluator, request, tmp_path)
    supervisor, child = Pipe()
    boundary = StoreBoundary(
        store,
        action="pause_after_commit",
        event_type=RunEventType.EVALUATION_ATTEMPT_STARTED,
        channel=child,
    )
    record = await store.get(request.run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(boundary, record), [])
    )
    try:
        with evaluation_scope(execution.evaluation_observer()):
            evaluating = asyncio.create_task(evaluator.evaluate(request))
        await asyncio.wait_for(asyncio.to_thread(supervisor.recv), 3)
        record = await store.get(request.run_id)
        state = SWEbenchAttemptState.model_validate(
            record.state.execution.evaluation.attempt.payload
        )
        assert not state.directory.exists()
        assert not (evaluator.executable.parent / "harness-call.json").exists()
        supervisor.send("continue")
        assert (await evaluating).passed
        assert state.directory.is_dir()
        assert (evaluator.executable.parent / "harness-call.json").exists()
    finally:
        supervisor.close()
        child.close()


@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_attempt_commit_uncertainty_stops_before_directory(
    evaluation_case, tmp_path, action
):
    evaluator, request, _artifacts = evaluation_case
    store, execution = await scope_for(evaluator, request, tmp_path)
    record = await store.get(request.run_id)
    boundary = StoreBoundary(
        store, action=action, event_type=RunEventType.EVALUATION_ATTEMPT_STARTED
    )
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(boundary, record), [])
    )
    with (
        evaluation_scope(execution.evaluation_observer()),
        pytest.raises(RunPersistenceError),
    ):
        await evaluator.evaluate(request)
    assert boundary.fired
    assert not (evaluator.executable.parent / "harness-call.json").exists()
    assert not (evaluator.settings.swebench_work_root / request.run_id.hex).exists()


async def test_inspection_never_deletes_and_cleanup_preserves_agent_and_neighbor(
    evaluation_case, tmp_path
):
    evaluator, request, _artifacts = evaluation_case
    _store, execution = await scope_for(evaluator, request, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        await evaluator.evaluate(request)
    state = SWEbenchAttemptState.model_validate(
        execution.state.execution.evaluation.attempt.payload
    )
    name = f"sweb.eval.{state.instance_id.lower()}.{state.harness_run_id}"
    path = evaluator.docker_executable.parent / "docker-scenario.json"
    scenario = json.loads(path.read_text())
    scenario["containers"] = [
        {"Id": "c" * 64, "Name": "/" + name, "Image": "sha256:owned"},
        {"Id": "d" * 64, "Name": "/miniagent-agent", "Image": "sha256:agent"},
        {
            "Id": "e" * 64,
            "Name": "/" + name + ".not-digits",
            "Image": "sha256:neighbor",
        },
    ]
    path.write_text(json.dumps(scenario))
    operation = execution.state.execution.active_operation
    facts = await inspect_attempt_resources(
        state,
        evaluator.connection,
        docker_executable=evaluator.docker_executable,
        host_executions=operation.executions,
    )
    assert len(facts.containers) == 1 and facts.host_ended
    assert len(json.loads(path.read_text())["containers"]) == 3
    await cleanup_attempt_resources(facts, deadline=monotonic() + 5)
    assert [item["Id"] for item in json.loads(path.read_text())["containers"]] == [
        "d" * 64,
        "e" * 64,
    ]


async def test_interrupted_evaluation_restarts_same_patch_after_owned_cleanup(
    evaluation_case, tmp_path
):
    evaluator, request, artifacts = evaluation_case
    _store, execution = await scope_for(evaluator, request, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        assert (await evaluator.evaluate(request)).passed
    old = SWEbenchAttemptState.model_validate(
        execution.state.execution.evaluation.attempt.payload
    )
    old_executions = execution.state.execution.active_operation.executions
    await execution.interrupt_operation(unconfirmed=False)
    facts = await inspect_attempt_resources(
        old,
        evaluator.connection,
        docker_executable=evaluator.docker_executable,
        host_executions=old_executions,
    )
    await cleanup_attempt_resources(facts, deadline=monotonic() + 5)
    with evaluation_scope(execution.evaluation_observer()):
        result = await evaluator.evaluate(request)
    new = SWEbenchAttemptState.model_validate(
        execution.state.execution.evaluation.attempt.payload
    )
    assert new.attempt_id != old.attempt_id
    assert new.patch_ref == old.patch_ref and new.patch_sha256 == old.patch_sha256
    assert await artifacts.read(new.patch_ref) == await artifacts.read(old.patch_ref)
    assert result.passed


@pytest.mark.parametrize(("identity_missing",), [(False,), (True,)])
async def test_live_or_unverifiable_harness_blocks_new_attempt(
    evaluation_case, tmp_path, identity_missing
):
    evaluator, request, _artifacts = evaluation_case
    _store, execution = await scope_for(evaluator, request, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        await evaluator.evaluate(request)
    state = SWEbenchAttemptState.model_validate(
        execution.state.execution.evaluation.attempt.payload
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        "import signal; print('ready'); signal.pause()",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 2) == b"ready\n"
        identity = identify_host_process(
            process.pid,
            attempt_id=state.attempt_id,
            execution_id=uuid4(),
            role="harness",
        )
        before = (
            evaluator.docker_executable.parent / "docker-calls.jsonl"
        ).read_bytes()
        facts = await inspect_attempt_resources(
            state,
            evaluator.connection,
            docker_executable=evaluator.docker_executable,
            host_executions=() if identity_missing else (identity,),
        )
        assert not facts.host_ended
        with pytest.raises(EvaluationAttemptBusy):
            await cleanup_attempt_resources(facts, deadline=monotonic() + 5)
        assert (
            evaluator.docker_executable.parent / "docker-calls.jsonl"
        ).read_bytes() == before
    finally:
        await reap_process(process, [], deadline=monotonic() + 5)


async def test_artifact_control_error_is_not_translated_to_evaluation_failure(
    evaluation_case, tmp_path
):
    evaluator, request, artifacts = evaluation_case
    _store, execution = await scope_for(evaluator, request, tmp_path)
    calls = []

    class UncertainArtifacts:
        async def get_record(self, ref):
            return await artifacts.get_record(ref)

        async def read(self, ref):
            return await artifacts.read(ref)

        async def list_for_run(self, run_id):
            return await artifacts.list_for_run(run_id)

        async def put(self, write, content):
            calls.append(write.type)
            raise RunPersistenceError(
                run_id=write.run_id,
                operation="artifact observer",
                last_confirmed_sequence=1,
            )

    evaluator.artifact_store = UncertainArtifacts()
    with (
        evaluation_scope(execution.evaluation_observer()),
        pytest.raises(RunPersistenceError),
    ):
        await evaluator.evaluate(request)
    assert calls == ["evaluation_predictions"]


async def test_ended_preflight_helpers_do_not_prove_harness_end(
    evaluation_case, tmp_path
):
    evaluator, request, _artifacts = evaluation_case
    _store, execution = await scope_for(evaluator, request, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        await evaluator.evaluate(request)
    state = SWEbenchAttemptState.model_validate(
        execution.state.execution.evaluation.attempt.payload
    )
    process = await asyncio.create_subprocess_exec(
        "sleep", "0.1", start_new_session=True
    )
    identity = identify_host_process(
        process.pid,
        attempt_id=state.attempt_id,
        execution_id=uuid4(),
        role="docker_helper",
    )
    await process.wait()
    calls = evaluator.docker_executable.parent / "docker-calls.jsonl"
    before = calls.read_bytes()
    facts = await inspect_attempt_resources(
        state,
        evaluator.connection,
        docker_executable=evaluator.docker_executable,
        host_executions=(identity,),
    )
    assert not facts.host_ended
    with pytest.raises(EvaluationAttemptBusy):
        await cleanup_attempt_resources(facts, deadline=monotonic() + 5)
    assert calls.read_bytes() == before
