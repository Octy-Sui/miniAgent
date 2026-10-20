import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.agents import AgentSpec
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.application import ApplicationDependencies
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
)
from miniagent.environments import (
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
)
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.policy import PolicyContext
from miniagent.runtime import RunRecoveryContext
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry


@pytest.fixture
async def submission_case(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    await asyncio.to_thread(
        subprocess.run, ["git", "init", "--quiet", str(source)], check=True
    )
    (source / "tracked.txt").write_text("original\n")
    await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(source), "add", "."], check=True
    )
    await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        check=True,
    )
    store = FileArtifactStore(tmp_path / "artifacts")
    workspace = await LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=store
    ).create(
        LocalWorkspaceSpec(run_id=uuid4(), options=LocalOptions(source_dir=source))
    )
    return workspace, store


class StaticProvider:
    async def generate(self, request):
        return ModelResponse(
            message=AssistantMessage(content="done"),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


@pytest.fixture
async def delivery_case(submission_case, tmp_path):
    workspace, store = submission_case
    journal = SQLiteRunStore(tmp_path / "journal.sqlite3")
    await journal.initialize()
    submission = CandidateSubmission(workspace, store)
    deps = ApplicationDependencies(
        agent=AgentSpec(
            name="test",
            system_prompt="Test.",
            generation=GenerationOptions(max_output_tokens=100),
            model=ModelTarget(provider="test", model="test-model"),
        ),
        task="issue",
        run_id=workspace.run_id,
        provider=StaticProvider(),
        tools=ToolRegistry([]),
        workspace=workspace,
        artifact_store=store,
        pricing=PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=0,
                    output_per_token=0,
                )
            ]
        ),
        policy_context=PolicyContext(
            workspace_id=workspace.workspace_id, workspace_kind="local", revision="test"
        ),
        approval_handler=None,
        run_store=journal,
        recovery_context=RunRecoveryContext(
            workspace_id=workspace.workspace_id,
            workspace_spec=persist_workspace_spec(workspace.spec),
        ),
        candidate_submission=submission,
    )
    return deps, submission


@pytest.fixture
def configured_host(
    tmp_path,
    monkeypatch,
    prepared_bundle,
    docker_cli,
    harness_executable,
    export_engine,
):
    (tmp_path / "docker").symlink_to(docker_cli)
    connection = export_engine(
        tmp_path / "docker", ("container", "exec", "--workdir", "/testbed", "a" * 64)
    )
    scenario_path = tmp_path / "docker-scenario.json"
    scenario = json.loads(scenario_path.read_text())
    scenario["daemon_id"] = connection.daemon_id
    scenario["container_id"] = "a" * 64
    for context in scenario["contexts"].values():
        context["Endpoints"]["docker"]["Host"] = connection.endpoint
    scenario_path.write_text(json.dumps(scenario))
    values = {
        "OPENAI_API_KEY": "test",
        "OPENAI_BASE_URL": "https://example.test",
        "SWEBENCH_DATA_ROOT": str(prepared_bundle),
        "SWEBENCH_WORK_ROOT": str(tmp_path / "runs"),
        "SWEBENCH_EXECUTABLE": str(harness_executable),
        "EVALUATION_DOCKER_EXECUTABLE": str(docker_cli),
        "ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "RUN_DATABASE_PATH": str(tmp_path / "journal.sqlite3"),
        "LOCAL_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return tmp_path


@pytest.fixture
async def resume_case(tmp_path, monkeypatch):
    from tests.support.cli.bootstrap import configure_settings, create_source_repository
    from tests.support.cli.resume import retained

    configure_settings(tmp_path, monkeypatch)
    source = create_source_repository(tmp_path / "source")
    return await retained(source, monkeypatch)
