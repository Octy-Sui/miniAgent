import json
import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.application import Application
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap, execute_new_run
from miniagent.cli.run_definition import parse_application_definition
from miniagent.environments import WorkspaceKind
from miniagent.environments.attachment import LocalAttachment
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
)
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.runtime import CancellationToken, RunResult
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.store_contracts import RunNotFoundError
from miniagent.storage import SQLiteRunStore
from miniagent.storage.run_lock import RunInUseError, RunLock
from tests.cli.test_bootstrap import configure_settings, create_source_repository
from tests.resume_support import StoreBoundary
from tests.storage.test_sqlite import make_state


@pytest.mark.parametrize(("allow_pull",), [(False,), (True,)])
async def test_missing_image_never_creates_resumable_run(
    configured_host, monkeypatch, allow_pull, capsys
):
    monkeypatch.setenv("SWEBENCH_ALLOW_IMAGE_PULL", str(allow_pull).lower())
    path = configured_host / "docker-scenario.json"
    scenario = json.loads(path.read_text())
    scenario["image_missing"] = True
    path.write_text(json.dumps(scenario))
    with pytest.raises(
        EvaluationPreparationError, match="EVALUATION_IMAGE_UNAVAILABLE"
    ):
        await execute_new_run(
            BootstrapConfig(
                agent=DEFAULT_AGENT,
                task="issue",
                source_dir=configured_host,
                workspace_kind=WorkspaceKind.DOCKER,
                swebench_task=SWEbenchTaskContext(instance_id="django__django-11477"),
            ),
            cancellation_token=CancellationToken(),
        )
    calls = [
        json.loads(line)["argv"]
        for line in (configured_host / "docker-calls.jsonl").read_text().splitlines()
    ]
    assert all("create" not in call and "pull" not in call for call in calls)
    assert not (configured_host / "journal.sqlite3").exists()
    assert "Resume:" not in capsys.readouterr().out


async def test_first_record_contains_complete_definition_before_publication(
    tmp_path, monkeypatch
):
    configure_settings(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-resume-fixture")
    run_id = uuid4()
    application = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="task",
            source_dir=create_source_repository(tmp_path / "source"),
            run_id=run_id,
        )
    )
    deps = application.dependencies
    assert deps.run_id == run_id
    with pytest.raises(RunNotFoundError):
        await deps.run_store.get(run_id)
    order = []

    def published(record):
        assert record.state.lifecycle.status.value == "created"
        context = record.recovery_context
        assert (
            context is not None
            and context.attachment is not None
            and context.application is not None
        )
        assert context.attachment.workspace_id == deps.workspace.workspace_id
        definition = parse_application_definition(
            context.application, record.state.definition
        )
        assert len(definition.pricing.models) == 1
        assert "secret-resume-fixture" not in record.model_dump_json()
        order.append("published")

    class Provider:
        async def generate(self, request):
            assert order == ["published"]
            order.append("provider")
            return ModelResponse(
                message=AssistantMessage(content="done"),
                finish_reason=FinishReason.STOP,
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    result = await Application(replace(deps, provider=Provider())).run(
        CancellationToken(), on_record_created=published
    )
    assert isinstance(result.run_result, RunResult)
    assert order == ["published", "provider"]


async def test_create_after_insert_failure_cleans_only_new_resources(
    tmp_path, monkeypatch, capsys
):
    configure_settings(tmp_path, monkeypatch)
    source = create_source_repository(tmp_path / "source")
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    existing = await store.create(make_state())
    neighbor = tmp_path / "workspaces" / "existing-neighbor"
    neighbor.mkdir(parents=True)
    (neighbor / "keep").write_text("preserve")
    target = uuid4()
    with sqlite3.connect(store.path) as connection:
        connection.execute(f"""CREATE TRIGGER fail_new_create AFTER INSERT ON runs
            WHEN NEW.run_id = '{target}'
            BEGIN SELECT CASE WHEN json_extract(NEW.recovery_context_json, '$.attachment.workspace_id') IS NOT NULL
            THEN RAISE(FAIL, 'after insert prefix hit') END; END""")
    with pytest.raises(RunPersistenceError) as caught:
        await execute_new_run(
            BootstrapConfig(
                agent=DEFAULT_AGENT, task="task", source_dir=source, run_id=target
            ),
            cancellation_token=CancellationToken(),
        )
    assert "after insert prefix hit" in str(caught.value.__cause__)
    assert await store.get(existing.state.identity.run_id) == existing
    assert (neighbor / "keep").read_text() == "preserve"
    with pytest.raises(RunNotFoundError):
        await store.get(target)
    assert not list((tmp_path / "workspaces" / target.hex).glob("*/repo"))
    assert "Resume:" not in capsys.readouterr().out


async def test_new_run_lock_is_acquired_before_workspace_creation(
    tmp_path, monkeypatch
):
    configure_settings(tmp_path, monkeypatch)
    source = create_source_repository(tmp_path / "source")
    run_id = uuid4()
    with RunLock(tmp_path / "runs.sqlite3", run_id), pytest.raises(RunInUseError):
        await execute_new_run(
            BootstrapConfig(
                agent=DEFAULT_AGENT, task="task", source_dir=source, run_id=run_id
            ),
            cancellation_token=CancellationToken(),
        )
    assert not (tmp_path / "workspaces").exists()


@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_uncertain_create_preserves_new_workspace_without_publication(
    tmp_path, monkeypatch, action
):
    configure_settings(tmp_path, monkeypatch)
    application = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="task",
            source_dir=create_source_repository(tmp_path / "source"),
        )
    )
    deps = application.dependencies
    boundary = StoreBoundary(deps.run_store, action=action, operation="create")
    published = []
    with pytest.raises(RunPersistenceError):
        await Application(replace(deps, run_store=boundary)).run(
            CancellationToken(), on_record_created=published.append
        )
    assert boundary.fired and published == []
    attachment = deps.workspace.describe_attachment()
    assert (
        isinstance(attachment, LocalAttachment) and attachment.repository_root.exists()
    )
    assert application.submission.result.status == "not_attempted"
    if action == "applied_then_error":
        assert (
            await deps.run_store.get(deps.run_id)
        ).state.identity.run_id == deps.run_id
    else:
        with pytest.raises(RunNotFoundError):
            await deps.run_store.get(deps.run_id)
    await deps.workspace.destroy()
