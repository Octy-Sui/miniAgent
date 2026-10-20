import hashlib
import json
import signal
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.artifacts import FileArtifactStore
from tests.support.resume.local import origin, stored


async def documents(root):
    store = FileArtifactStore(root / "artifacts")
    run_id = UUID(origin(root)["run_id"])
    records = (await store.list_records_for_run(run_id)).records
    exports = [item for item in records if item.type == "run_trajectory"]
    values = []
    for record in exports:
        body = await store.read(record.reference())
        assert record.size_bytes == len(body)
        assert record.sha256 == hashlib.sha256(body).hexdigest()
        values.append((record.reference(), body, json.loads(body)))
    return sorted(values, key=lambda value: value[2]["boundary"]["through_sequence"])


async def test_resume_creates_new_trajectory_without_rewriting_old(spawn_worker):
    first = await spawn_worker(trajectory=True, gate_call=1)
    await first.boundary("approval")
    first.process.send_signal(signal.SIGINT)
    code, output = await first.finish()
    assert code == 130, output
    initial = await documents(first.root)
    assert len(initial) == 1
    ref, original, before = initial[0]
    assert before["boundary"]["kind"] == "suspended"
    resumed = await spawn_worker(
        "resume", label="resumed", run_id=origin(first.root)["run_id"]
    )
    code, output = await resumed.finish()
    assert code == 0, output
    exports = await documents(first.root)
    assert len(exports) == 2
    after = exports[1][2]
    assert before["run_id"] == after["run_id"]
    assert (
        before["boundary"]["through_sequence"] < after["boundary"]["through_sequence"]
    )
    assert await FileArtifactStore(first.root / "artifacts").read(ref) == original
    assert exports[0][0].artifact_id != exports[1][0].artifact_id
    assert after["summary"]["final_output"] == "resume-local-ok"
    assert not Path(origin(first.root)["repository_root"]).exists()
    ended = [
        e["payload"] for e in after["events"] if e["payload"]["type"] == "tool_end"
    ]
    assert len(ended) == 3
    assert all(e["observation"] and e["raw_result"] for e in ended)
    artifacts = FileArtifactStore(first.root / "artifacts")
    for event in after["events"]:
        if event["payload"]["type"] == "operation_output_recorded":
            from miniagent.artifacts import ArtifactRef

            for stream in ("stdout", "stderr"):
                await artifacts.read(
                    ArtifactRef.model_validate(
                        event["payload"]["output"][stream]["ref"]
                    )
                )
    assert all(item["type"] != "run_trajectory" for item in after["artifacts"])


@pytest.mark.parametrize(
    "event", ["model_requested", "model_responded", "tool_execution_start"]
)
async def test_committed_response_boundary_reopens_without_unsafe_replay(
    spawn_worker, event
):
    worker = await spawn_worker(trajectory=True, fault_event=event)
    boundary = await worker.boundary("commit")
    record, events = await stored(worker.root)
    assert record.last_event_sequence == boundary["sequence"]
    assert any(item.type.value == event for item in events)
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    next_worker = await spawn_worker(
        "resume", label="reopened", run_id=origin(worker.root)["run_id"]
    )
    code, output = await next_worker.finish()
    if event == "tool_execution_start":
        assert code == 1 and "UNCERTAIN_TOOL_EXECUTION" in output
        assert (
            Path(origin(worker.root)["repository_root"]) / "effects.txt"
        ).read_text() == ""
        assert not await documents(worker.root)
    else:
        assert code == 0, output
        exports = await documents(worker.root)
        assert len(exports) == 1
        document = exports[0][2]
        assert document["boundary"]["terminal"]
        assert [e["sequence"] for e in document["events"]] == list(
            range(1, document["boundary"]["through_sequence"] + 1)
        )
        assert document["summary"]["unconfirmed"] == (
            1 if event == "model_requested" else 0
        )
        assert (
            len([e for e in document["events"] if e["payload"]["type"] == "tool_end"])
            == 3
        )


@pytest.mark.parametrize("stage", ["before_return", "during_transaction"])
async def test_uncommitted_response_is_not_recovered_as_committed(spawn_worker, stage):
    worker = await spawn_worker(
        trajectory=True,
        before_response=stage == "before_return",
        transaction_event="model_responded" if stage == "during_transaction" else None,
    )
    barrier = await worker.boundary(
        "provider_return" if stage == "before_return" else "transaction"
    )
    _record, events = await stored(worker.root)
    assert barrier["time"] > 0
    assert not any(event.type.value == "model_responded" for event in events)
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    reopened = await spawn_worker(
        "resume", label="reopened", run_id=origin(worker.root)["run_id"]
    )
    code, output = await reopened.finish()
    assert code == 0, output
    document = (await documents(worker.root))[0][2]
    assert document["summary"]["unconfirmed"] == 1
    assert (
        len(
            [
                event
                for event in document["events"]
                if event["payload"]["type"] == "tool_end"
            ]
        )
        == 3
    )
    assert (
        len(
            [
                event
                for event in document["events"]
                if event["payload"]["type"] == "model_responded"
            ]
        )
        == 2
    )


@pytest.mark.parametrize("stage", ["encoding", "body", "manifest"])
async def test_repeated_signal_drains_export_before_releasing_run_lock(
    spawn_worker, stage
):
    import asyncio

    from miniagent.storage.run_lock import RunInUseError, RunLock

    worker = await spawn_worker(trajectory=True, trajectory_gate=stage)
    await worker.boundary("trajectory_" + stage)
    run_id = UUID(origin(worker.root)["run_id"])
    with pytest.raises(RunInUseError), RunLock(worker.root / "runs.sqlite3", run_id):
        pass
    worker.process.send_signal(signal.SIGINT)
    await worker.boundary("first_signal")
    worker.process.send_signal(signal.SIGINT)
    if stage != "encoding":
        # A started filesystem action must settle while ownership is still held.
        with (
            pytest.raises(RunInUseError),
            RunLock(worker.root / "runs.sqlite3", run_id),
        ):
            pass
        worker.release()
    code, output = await worker.finish()
    assert code == 130, output
    exports = await documents(worker.root)
    assert len(exports) == (1 if stage == "manifest" else 0)
    frozen_files = {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (worker.root / "artifacts").rglob("*")
        if path.is_file()
    }
    await asyncio.sleep(0.05)
    assert {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in (worker.root / "artifacts").rglob("*")
        if path.is_file()
    } == frozen_files
    with RunLock(worker.root / "runs.sqlite3", run_id):
        pass


async def test_failed_summary_then_later_compaction_survives_process_reopen(
    tmp_path, monkeypatch, provider_endpoint
):
    import asyncio
    import os
    import sys

    from miniagent.artifacts import ArtifactRef
    from miniagent.storage import SQLiteRunStore

    root = tmp_path / "summary-history"

    async def run(*arguments, environment=None):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            *arguments,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
        assert process.returncode == 0, stderr.decode() + stdout.decode()
        return stdout

    await run(
        "-m",
        "tests.support.trajectory.history",
        str(root),
        "512",
        "1024",
        "--soft-summary-failure",
    )
    exported = json.loads(
        await run("-m", "tests.support.trajectory.resource_worker", str(root), "export")
    )
    first_ref = ArtifactRef.model_validate(exported["ref"])
    artifacts = FileArtifactStore(root / "artifacts")
    first_bytes = await artifacts.read(first_ref)
    first = json.loads(first_bytes)
    failed = next(
        event
        for event in first["events"]
        if event["payload"]["type"] == "compaction_failed"
    )
    completed = [
        event
        for event in first["events"]
        if event["payload"]["type"] == "compaction_completed"
    ]
    assert completed and all(
        event["sequence"] > failed["sequence"] for event in completed
    )
    attempts = [
        event
        for event in first["events"]
        if event["payload"]["type"] == "compaction_attempt_recorded"
    ]
    assert (
        attempts[0]["payload"]["response"]["message"]["content"]
        == "invalid summary body"
    )
    assert first["coverage"]["status"] == "complete"
    run_id = UUID(first["run_id"])
    before = await SQLiteRunStore(root / "runs.db").get(run_id)
    environment = dict(
        os.environ,
        OPENAI_API_KEY="resource-fixture",
        OPENAI_BASE_URL=provider_endpoint.url,
    )
    await run(
        "-m",
        "miniagent.cli.main",
        "resume",
        "--run-id",
        str(run_id),
        "--database",
        str(root / "runs.db"),
        environment=environment,
    )
    after = await SQLiteRunStore(root / "runs.db").get(run_id)
    assert after.state.usage.input_tokens == before.state.usage.input_tokens + 10
    assert after.state.usage.output_tokens == before.state.usage.output_tokens + 5
    assert await artifacts.read(first_ref) == first_bytes
    exports = [
        record
        for record in (await artifacts.list_records_for_run(run_id)).records
        if record.type == "run_trajectory"
    ]
    assert len(exports) == 2
    second_ref = next(
        record.reference()
        for record in exports
        if record.artifact_id != first_ref.artifact_id
    )
    second = json.loads(await artifacts.read(second_ref))
    assert (
        second["boundary"]["through_sequence"] > first["boundary"]["through_sequence"]
    )
    assert len(
        [
            event
            for event in second["events"]
            if event["payload"]["type"] == "compaction_attempt_recorded"
        ]
    ) == len(attempts)
    evidence = Path(".superpowers/sdd/2026-10-20-trajectory")
    (evidence / "offline-summary-review.json").write_text(
        json.dumps(
            {
                "first_ref": first_ref.model_dump(mode="json"),
                "second_ref": second_ref.model_dump(mode="json"),
                "failed_sequence": failed["sequence"],
                "completed_sequences": [event["sequence"] for event in completed],
                "attempt_ids": [event["payload"]["attempt_id"] for event in attempts],
                "summary_calls_not_repeated": True,
                "human_acceptance": "NOT_ACCEPTED",
            },
            indent=2,
        )
    )
