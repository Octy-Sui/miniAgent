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
            trajectory=enabled,
        )
    )
    return app


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


@pytest.mark.parametrize("original", ["success", "failed", "exception"])
@pytest.mark.parametrize("failure", ["storage", "timeout"])
async def test_export_failure_preserves_all_original_outcomes(
    tmp_path, monkeypatch, capsys, original, failure
):
    import os
    import sqlite3
    import time
    from pathlib import Path

    from miniagent.artifacts import ArtifactWrite
    from miniagent.cli.rendering import print_trajectory_exception
    from miniagent.runtime.recording import RunPersistenceError

    app = await application(tmp_path, monkeypatch, failed=original == "failed")
    await app.dependencies.artifact_store.put(
        ArtifactWrite(run_id=app.dependencies.run_id, type="report"), b"report"
    )
    if original == "exception":
        with sqlite3.connect(app.dependencies.run_store.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_original BEFORE INSERT ON run_events WHEN NEW.type='model_responded' BEGIN SELECT RAISE(ABORT, 'original failure'); END"
            )
    if failure == "storage":
        original_replace = os.replace

        def fail_manifest(source, target):
            if (
                str(target).endswith(".json")
                and json.loads(Path(source).read_bytes()).get("type")
                == "run_trajectory"
            ):
                raise OSError("export disk unavailable")
            return original_replace(source, target)

        monkeypatch.setattr(os, "replace", fail_manifest)
    else:
        app = Application(
            replace(app.dependencies, artifact_collection_timeout_seconds=0.02)
        )
        read_text = Path.read_text
        reads = [0]

        def slow_export_catalog(path, *args, **kwargs):
            if "manifests" in path.parts:
                reads[0] += 1
                # The exception path skips original finalization, so its first read is export.
                if reads[0] >= (1 if original == "exception" else 2):
                    time.sleep(0.05)
            return read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", slow_export_catalog)
    if original == "exception":
        with pytest.raises(RunPersistenceError) as caught:
            await app.run(CancellationToken())
        print_trajectory_exception(caught.value)
        assert "TRAJECTORY_EXPORT_FAILED" in capsys.readouterr().err
        assert caught.value.__cause__ is not None
    else:
        result = await app.run(CancellationToken())
        assert result.run_result.status == (
            RunStatus.SUCCEEDED if original == "success" else RunStatus.FAILED
        )
        assert result.trajectory_export.status == "failed"
        assert result.finalization_errors == ()
    from miniagent.environments import LocalWorkspace

    workspace = app.dependencies.workspace
    assert isinstance(workspace, LocalWorkspace)
    assert workspace.root.exists() == (original != "success")


async def test_managed_execution_exception_exports_without_diagnostic_commands(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime

    import psutil

    from miniagent.environments.managed_execution import ManagedExecutionError
    from miniagent.models.protocol import ModelUsage
    from miniagent.policy import ApprovalOutcome, ApprovalResolution
    from tests.support.runtime.runtime import QueueProvider, tool_response

    app = await application(tmp_path, monkeypatch)

    class Human:
        async def resolve(self, request, decision):
            return ApprovalResolution(
                approval_id=request.approval_id,
                outcome=ApprovalOutcome.APPROVED,
                actor_kind="human",
                actor_id="fixture",
                reason="owned command",
                resolved_at=datetime.now(UTC),
            )

    provider = QueueProvider(
        [
            tool_response(
                name="shell", arguments={"command": "printf should-not-run"}
            ).model_copy(
                update={
                    "usage": ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                }
            )
        ]
    )
    app = Application(
        replace(app.dependencies, provider=provider, approval_handler=Human())
    )

    def unavailable_birth_identity(self):
        raise OSError("OS birth identity unavailable")

    monkeypatch.setattr(psutil.Process, "create_time", unavailable_birth_identity)
    with pytest.raises(ManagedExecutionError) as caught:
        await app.run(CancellationToken())
    assert any(note.startswith("Trajectory: ") for note in caught.value.__notes__)
    events = await app.dependencies.run_store.list_events(app.dependencies.run_id)
    assert not any(
        event.type.value == "diagnostic_finalization_started" for event in events
    )
    assert len(provider.requests) == 1
    from miniagent.environments import LocalWorkspace

    workspace = app.dependencies.workspace
    assert isinstance(workspace, LocalWorkspace)
    assert workspace.root.exists()


async def test_patch_interruption_exports_original_terminal_boundary(
    tmp_path, monkeypatch
):
    import os

    from miniagent.runtime import InterruptionToken

    app = await application(tmp_path, monkeypatch)
    (app.dependencies.workspace.root / "source.txt").write_text("changed\n")
    token = InterruptionToken()
    read = os.read
    reached = [False]

    def request_after_patch_bytes(fd, size):
        content = read(fd, size)
        if b"diff --git" in content and not reached[0]:
            reached[0] = True
            token.request(2)
        return content

    monkeypatch.setattr(os, "read", request_after_patch_bytes)
    result = await app.run(CancellationToken(), interruption_token=token)
    assert reached[0], "Patch receive barrier was not reached"
    assert result.run_result.status == RunStatus.SUCCEEDED
    assert any(
        error.code == "FINALIZATION_INTERRUPTED" for error in result.finalization_errors
    )
    assert result.trajectory_export.status == "exported"
    assert app.dependencies.workspace.root.exists()


async def test_effective_definition_uses_frozen_values_after_ambient_config_changes(
    tmp_path, monkeypatch
):
    from decimal import Decimal

    from miniagent.agents import AgentSpec
    from miniagent.cli import bootstrap as composition
    from miniagent.cli.run_definition import (
        freeze_application_definition,
        parse_application_definition,
    )
    from miniagent.cli.trajectory import try_export_trajectory
    from miniagent.models.config import GenerationOptions, ModelTarget
    from miniagent.runtime.state import RunDefinition
    from miniagent.storage import SQLiteRunStore
    from miniagent.storage.run_lock import RunLock

    configure_settings(tmp_path, monkeypatch)
    limits, prices = tmp_path / "limits.toml", tmp_path / "prices.toml"
    limits.write_text(
        '[[models]]\nprovider="qwen"\nmodel="deepseek-v4-pro-0813"\ncontext_window_tokens=12345\nmax_output_tokens=2048\n'
    )
    prices.write_text(
        '[[models]]\nprovider="qwen"\nmodel="deepseek-v4-pro-0813"\ninput_per_unit="3"\noutput_per_unit="7"\n'
    )
    monkeypatch.setenv("MODEL_LIMITS_FILE", str(limits))
    monkeypatch.setenv("MODEL_PRICING_FILE", str(prices))
    monkeypatch.setattr(
        composition,
        "build_provider",
        lambda agent, settings: StaticProvider(FinishReason.STOP),
    )
    spec = AgentSpec(
        name="frozen-agent",
        system_prompt="frozen instruction",
        model=ModelTarget(provider="qwen", model="deepseek-v4-pro-0813"),
        generation=GenerationOptions(max_output_tokens=321, temperature=0.2),
        allowed_tools=("shell",),
    )
    app = await bootstrap(
        BootstrapConfig(
            agent=spec,
            task="frozen task",
            source_dir=create_source_repository(tmp_path / "source"),
            trajectory=True,
        )
    )
    deps = app.dependencies
    assert isinstance(deps.run_store, SQLiteRunStore)
    assert deps.recovery_context.application is not None
    old_definition = RunDefinition(
        agent_spec=deps.agent,
        config=deps.config,
        tool_definitions=tuple(deps.tools.definitions(spec.allowed_tools)),
        tool_contract_metadata=tuple(
            deps.tools.contract_metadata(name) for name in spec.allowed_tools
        ),
    )
    frozen = parse_application_definition(
        deps.recovery_context.application, old_definition
    )
    config = deps.config.model_copy(
        update={"max_steps": 17, "approval_timeout_seconds": 12}
    )
    new_definition = old_definition.model_copy(update={"config": config})
    context = deps.recovery_context.model_copy(
        update={"application": freeze_application_definition(frozen, new_definition)}
    )
    assert context.attachment is not None
    app = Application(replace(deps, config=config, recovery_context=context))
    with RunLock(deps.run_store.path, deps.run_id):
        result = await app.run(CancellationToken())
    assert result.trajectory_export.status == "exported"
    monkeypatch.setenv(
        "MODEL_LIMITS_FILE", str(tmp_path / "unavailable-current-limits")
    )
    monkeypatch.setenv(
        "MODEL_PRICING_FILE", str(tmp_path / "unavailable-current-pricing")
    )
    monkeypatch.setenv("OPENAI_API_KEY", "forbidden-current-credential")
    monkeypatch.setenv("EXPORT_TRAJECTORY", "false")
    with RunLock(deps.run_store.path, deps.run_id):
        outcome = await try_export_trajectory(
            enabled=True,
            run_id=deps.run_id,
            run_store=deps.run_store,
            artifact_store=deps.artifact_store,
            definition=frozen,
            timeout_seconds=30,
            cleanup_deadline=None,
        )
    assert outcome.status == "exported"
    content = await deps.artifact_store.read(outcome.ref)
    projected = json.loads(content)["definition"]
    assert projected["task"] == "frozen task"
    assert projected["agent"]["name"] == "frozen-agent"
    assert projected["agent"]["system_prompt"] == "frozen instruction"
    assert projected["agent"]["model"] == {
        "provider": "qwen",
        "model": "deepseek-v4-pro-0813",
    }
    assert projected["agent"]["generation"]["max_output_tokens"] == 2048
    assert projected["agent"]["generation"]["temperature"] == 0.2
    assert projected["tools"][0]["name"] == "shell"
    assert (
        projected["tools"][0]["input_schema"]["properties"]["command"]["type"]
        == "string"
    )
    assert projected["budgets"]["max_steps"] == 17
    assert projected["budgets"]["approval_timeout_seconds"] == 12
    assert projected["model_limits"] == {
        "context_window_tokens": 12345,
        "max_output_tokens": 2048,
    }
    assert Decimal(projected["pricing"]["models"][0]["input_per_token"]) == Decimal(
        "0.000003"
    )
    assert Decimal(projected["pricing"]["models"][0]["output_per_token"]) == Decimal(
        "0.000007"
    )
    assert projected["workspace_kind"] == "local"
    assert projected["base_revision"] == context.attachment.base_revision
    assert projected["evaluator"] == "pass_through" and projected["benchmark"] is None
    assert b"forbidden-current-credential" not in content
