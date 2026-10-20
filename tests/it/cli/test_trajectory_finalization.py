import json
from dataclasses import replace

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.application import Application, ApplicationResult
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.models.protocol import FinishReason
from miniagent.runtime import CancellationToken, RunStatus
from tests.support.cli.bootstrap import (
    StaticProvider,
    configure_settings,
    create_source_repository,
)


async def application(tmp_path, monkeypatch, *, enabled=True, failed=False):
    from miniagent.cli import bootstrap as module

    configure_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        module,
        "build_provider",
        lambda agent, settings: StaticProvider(
            FinishReason.UNKNOWN if failed else FinishReason.STOP
        ),
    )
    app = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="task",
            source_dir=create_source_repository(tmp_path / "source"),
        )
    )
    return Application(replace(app.dependencies, export_trajectory=enabled))


@pytest.mark.parametrize("failed", [False, True])
async def test_export_does_not_change_run_outcome(tmp_path, monkeypatch, failed):
    app = await application(tmp_path, monkeypatch, failed=failed)
    result = await app.run(CancellationToken())
    assert result.run_result.status == (
        RunStatus.FAILED if failed else RunStatus.SUCCEEDED
    )
    assert result.trajectory_export.status == "exported"
    document = json.loads(
        await app.dependencies.artifact_store.read(result.trajectory_export.ref)
    )
    record = await app.dependencies.run_store.get(result.run_result.run_id)
    assert document["boundary"]["through_sequence"] == record.last_event_sequence
    assert document["summary"]["final_output"] == record.state.lifecycle.final_output
    assert result.finalization_errors == ()


async def test_export_failure_is_nonfatal(tmp_path, monkeypatch):
    import miniagent.trajectory.export as module

    app = await application(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "MAX_JSON_BYTES", 1)
    result = await app.run(CancellationToken())
    assert result.run_result.status == RunStatus.SUCCEEDED
    assert result.trajectory_export.status == "failed"
    assert result.trajectory_export.code == "TRAJECTORY_TOO_LARGE"
    assert result.finalization_errors == ()
    assert not app.dependencies.workspace.root.exists()


async def test_disabled_does_not_register_trajectory(tmp_path, monkeypatch):
    app = await application(tmp_path, monkeypatch, enabled=False)
    result = await app.run(CancellationToken())
    assert result.trajectory_export.status == "disabled"
    page = await app.dependencies.artifact_store.list_records_for_run(
        result.run_result.run_id
    )
    assert not any(record.type == "run_trajectory" for record in page.records)


async def test_runtime_persistence_exception_keeps_original_and_exports_once(
    tmp_path, monkeypatch
):
    import sqlite3

    from miniagent.runtime.recording import RunPersistenceError

    app = await application(tmp_path, monkeypatch)
    with sqlite3.connect(app.dependencies.run_store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_response BEFORE INSERT ON run_events WHEN NEW.type='model_responded' BEGIN SELECT RAISE(ABORT, 'response unavailable'); END"
        )
    with pytest.raises(RunPersistenceError) as caught:
        await app.run(CancellationToken())
    assert any(note.startswith("Trajectory: ") for note in caught.value.__notes__)
    page = await app.dependencies.artifact_store.list_records_for_run(
        app.dependencies.run_id
    )
    trajectories = [item for item in page.records if item.type == "run_trajectory"]
    assert len(trajectories) == 1
    assert app.dependencies.workspace.root.exists()
    document = json.loads(
        await app.dependencies.artifact_store.read(trajectories[0].reference())
    )
    assert not any(
        event["payload"]["type"] == "model_responded" for event in document["events"]
    )


async def test_manifest_failure_is_nonfatal_and_does_not_publish_uri(
    tmp_path, monkeypatch
):
    from pathlib import Path

    app = await application(tmp_path, monkeypatch)
    import os

    original = os.replace

    def fail_trajectory_manifest(path, target):
        if (
            str(target).endswith(".json")
            and Path(path).is_file()
            and json.loads(Path(path).read_bytes()).get("type") == "run_trajectory"
        ):
            raise OSError("trajectory disk failure")
        return original(path, target)

    monkeypatch.setattr(os, "replace", fail_trajectory_manifest)
    result = await app.run(CancellationToken())
    assert result.run_result.status == RunStatus.SUCCEEDED
    assert result.trajectory_export.status == "failed"
    assert result.finalization_errors == ()
    assert not app.dependencies.workspace.root.exists()


async def test_metadata_collection_keeps_candidate_patch_validation(delivery_case):
    from miniagent.artifacts import ArtifactWrite

    deps, submission = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    submitted = await submission.submit(kind="candidate")
    assert submitted.patch_ref is not None
    unrelated = await deps.artifact_store.put(
        ArtifactWrite(run_id=deps.run_id, type="tool_stdout"), b"large" * 10000
    )
    from pathlib import Path
    from urllib.parse import urlparse

    Path(urlparse(unrelated.uri).path).unlink()
    result = await Application(deps).run(CancellationToken())
    assert result.finalization_errors == ()
    assert any(ref.artifact_id == unrelated.artifact_id for ref in result.artifacts)


async def test_corrupt_candidate_patch_still_fails_collection(delivery_case):
    from pathlib import Path
    from urllib.parse import urlparse

    deps, submission = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    submitted = await submission.submit(kind="candidate")
    Path(urlparse(submitted.patch_ref.uri).path).write_bytes(b"corrupt")
    result = await Application(deps).run(CancellationToken())
    assert any(
        error.code in ("ARTIFACT_COLLECTION_FAILED", "PATCH_ARTIFACT_INVALID")
        for error in result.finalization_errors
    )
    assert deps.workspace.root.exists()


async def test_exhausted_deadline_skips_without_registering(tmp_path, monkeypatch):
    from time import monotonic

    from miniagent.cli.run_definition import parse_application_definition
    from miniagent.cli.trajectory import try_export_trajectory

    app = await application(tmp_path, monkeypatch, enabled=False)
    result = await app.run(CancellationToken())
    deps = app.dependencies
    record = await deps.run_store.get(deps.run_id)
    definition = parse_application_definition(
        deps.recovery_context.application, record.state.definition
    )
    outcome = await try_export_trajectory(
        enabled=True,
        run_id=deps.run_id,
        run_store=deps.run_store,
        artifact_store=deps.artifact_store,
        definition=definition,
        timeout_seconds=30,
        cleanup_deadline=monotonic() - 1,
    )
    assert outcome.status == "skipped"
    assert result.run_result.status == RunStatus.SUCCEEDED
    assert not any(
        item.type == "run_trajectory"
        for item in (
            await deps.artifact_store.list_records_for_run(deps.run_id)
        ).records
    )


async def test_started_manifest_publication_settles_after_deadline(
    tmp_path, monkeypatch
):
    import asyncio
    import os
    import threading
    from pathlib import Path
    from time import monotonic

    from miniagent.cli.run_definition import parse_application_definition
    from miniagent.cli.trajectory import try_export_trajectory

    app = await application(tmp_path, monkeypatch, enabled=False)
    await app.run(CancellationToken())
    deps = app.dependencies
    record = await deps.run_store.get(deps.run_id)
    definition = parse_application_definition(
        deps.recovery_context.application, record.state.definition
    )
    entered, release = threading.Event(), threading.Event()
    original = os.replace

    def hold_manifest(source, target):
        if (
            str(target).endswith(".json")
            and json.loads(Path(source).read_bytes()).get("type") == "run_trajectory"
        ):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("manifest barrier not released")
        return original(source, target)

    monkeypatch.setattr(os, "replace", hold_manifest)
    deadline = monotonic() + 0.3
    task = asyncio.create_task(
        try_export_trajectory(
            enabled=True,
            run_id=deps.run_id,
            run_store=deps.run_store,
            artifact_store=deps.artifact_store,
            definition=definition,
            timeout_seconds=10,
            cleanup_deadline=deadline,
        )
    )
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        await asyncio.sleep(max(0, deadline - monotonic()) + 0.02)
    finally:
        release.set()
    outcome = await task
    assert outcome.status == "exported"
    assert (
        json.loads(await deps.artifact_store.read(outcome.ref))["kind"]
        == "miniagent.trajectory"
    )


async def test_resume_preparation_suspension_exports_frozen_boundary(
    resume_case, monkeypatch
):
    import asyncio
    import sqlite3

    from miniagent.cli.resume import ResumeConfig, resume_application
    from miniagent.cli.run_definition import (
        freeze_application_definition,
        parse_application_definition,
    )
    from miniagent.runtime import InterruptionToken
    from tests.support.trajectory.barriers import SQLiteWriteBarrier

    app, _execution, provider = resume_case
    deps = app.dependencies
    record = await deps.run_store.get(deps.run_id)
    frozen = parse_application_definition(
        record.recovery_context.application, record.state.definition
    ).model_copy(update={"export_trajectory": True})
    context = record.recovery_context.model_copy(
        update={
            "application": freeze_application_definition(
                frozen, record.state.definition
            )
        }
    )
    with sqlite3.connect(deps.run_store.path) as connection:
        connection.execute(
            "UPDATE runs SET recovery_context_json=? WHERE run_id=?",
            (context.model_dump_json(), str(deps.run_id)),
        )
    token = InterruptionToken()
    barrier = SQLiteWriteBarrier("resume_started")
    monkeypatch.setattr(sqlite3, "connect", barrier.connector(sqlite3.connect))
    running = asyncio.create_task(
        resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
            interruption_token=token,
        )
    )
    try:
        assert await asyncio.to_thread(barrier.entered.wait, 3)
        token.request(2)
    finally:
        barrier.release.set()
    result = await asyncio.wait_for(running, 5)
    assert isinstance(result, ApplicationResult)
    assert result.run_result.status == RunStatus.SUSPENDED
    assert result.trajectory_export.status == "exported"
    assert provider.requests == []
    latest = await deps.run_store.get(deps.run_id)
    document = json.loads(await deps.artifact_store.read(result.trajectory_export.ref))
    assert document["boundary"]["through_sequence"] == latest.last_event_sequence
    assert document["boundary"]["kind"] == "suspended"


@pytest.mark.parametrize(("timeout", "cleanup_budget"), [(0.1, 30), (30, 0.1)])
async def test_total_budget_spans_all_manifest_pages(
    tmp_path, monkeypatch, timeout, cleanup_budget
):
    import asyncio
    from time import monotonic

    from miniagent.artifacts import ArtifactWrite
    from miniagent.cli.run_definition import parse_application_definition
    from miniagent.cli.trajectory import try_export_trajectory

    app = await application(tmp_path, monkeypatch, enabled=False)
    await app.run(CancellationToken())
    deps = app.dependencies
    for index in range(201):
        await deps.artifact_store.put(
            ArtifactWrite(run_id=deps.run_id, type="report", name=str(index)), b""
        )
    record = await deps.run_store.get(deps.run_id)
    definition = parse_application_definition(
        deps.recovery_context.application, record.state.definition
    )

    class SlowCatalog(FileArtifactStore):
        pages = 0

        def __getattr__(self, name):
            return getattr(deps.artifact_store, name)

        async def list_records_for_run(self, *args, **kwargs):
            self.pages += 1
            await asyncio.sleep(0.06)
            return await deps.artifact_store.list_records_for_run(*args, **kwargs)

    artifacts = SlowCatalog(deps.artifact_store.root)
    outcome = await try_export_trajectory(
        enabled=True,
        run_id=deps.run_id,
        run_store=deps.run_store,
        artifact_store=artifacts,
        definition=definition,
        timeout_seconds=timeout,
        cleanup_deadline=monotonic() + cleanup_budget,
    )
    assert artifacts.pages == 2
    assert outcome.status == "failed"
    page = await deps.artifact_store.list_records_for_run(deps.run_id, limit=1000)
    assert not any(record.type == "run_trajectory" for record in page.records)
