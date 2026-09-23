import subprocess
from pathlib import Path
from typing import cast

import pytest

from miniagent.agents.default import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.bootstrap import (
    BootstrapConfig,
    bootstrap,
    create_workspace,
)
from miniagent.environments import (
    LocalWorkspace,
    LocalWorkspaceState,
)
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from miniagent.runtime import CancellationToken, RunStatus
from miniagent.tools.shell import ShellArguments


def create_source_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "-C", str(path), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    (path / "source.txt").write_text("source\n", encoding="utf-8")
    return path


class StaticProvider(ModelProvider):
    def __init__(self, finish_reason: FinishReason) -> None:
        self.finish_reason = finish_reason

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            message=AssistantMessage(content="finished"),
            finish_reason=self.finish_reason,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def configure_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RUN_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv(
        "MODEL_PRICING_FILE",
        str(Path.cwd() / "config" / "model_pricing.toml"),
    )


async def test_application_persists_patch_and_cleans_successful_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    configure_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "miniagent.cli.bootstrap.build_provider",
        lambda agent, settings: StaticProvider(FinishReason.STOP),
    )
    config = BootstrapConfig(
        agent=DEFAULT_AGENT,
        task="create a result file",
        source_dir=create_source_repository(tmp_path / "source"),
    )

    application = await bootstrap(config)
    workspace = application.dependencies.workspace
    result = await application.dependencies.tools["shell"].execute(
        ShellArguments(command="printf result > result.txt"),
        tool_call_id="write-result",
    )
    assert result.error is None
    application_result = await application.run(CancellationToken())

    assert application_result.artifacts
    patch_artifact = application_result.artifacts[0]
    artifact_store = FileArtifactStore(artifact_root)
    patch = await artifact_store.read(patch_artifact)
    assert b"result.txt" in patch
    record = await artifact_store.get_record(patch_artifact)
    assert record.metadata["files"] == ["result.txt"]
    assert not (config.source_dir / "result.txt").exists()
    assert cast(LocalWorkspace, workspace).state is LocalWorkspaceState.DESTROYED


@pytest.mark.parametrize(
    ("finish_reason", "expected_status", "expected_destroyed"),
    [
        (FinishReason.STOP, RunStatus.SUCCEEDED, True),
        (FinishReason.LENGTH, RunStatus.FAILED, False),
    ],
)
async def test_bootstrap_destroys_workspace_only_after_successful_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finish_reason: FinishReason,
    expected_status: RunStatus,
    expected_destroyed: bool,
) -> None:
    configure_settings(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "miniagent.cli.bootstrap.build_provider",
        lambda agent, settings: StaticProvider(finish_reason),
    )
    config = BootstrapConfig(
        agent=DEFAULT_AGENT,
        task="finish",
        source_dir=create_source_repository(tmp_path / "source"),
    )

    application = await bootstrap(config)
    workspace = application.dependencies.workspace
    result = await application.run(CancellationToken())

    local_workspace = cast(LocalWorkspace, workspace)
    assert result.run_result.status is expected_status
    assert (local_workspace.state is LocalWorkspaceState.DESTROYED) is (
        expected_destroyed
    )
    assert local_workspace.root.exists() is not expected_destroyed
    if not expected_destroyed:
        await local_workspace.destroy()


async def test_bootstrap_destroys_workspace_when_assembly_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_settings(tmp_path, monkeypatch)
    workspaces: list[LocalWorkspace] = []
    original_create_workspace = create_workspace

    async def recording_create_workspace(spec, settings) -> LocalWorkspace:
        workspace = cast(
            LocalWorkspace,
            await original_create_workspace(spec, settings),
        )
        workspaces.append(workspace)
        return workspace

    def fail_provider_assembly(agent, settings) -> ModelProvider:
        raise RuntimeError("assembly failed")

    monkeypatch.setattr(
        "miniagent.cli.bootstrap.create_workspace",
        recording_create_workspace,
    )
    monkeypatch.setattr(
        "miniagent.cli.bootstrap.build_provider",
        fail_provider_assembly,
    )
    config = BootstrapConfig(
        agent=DEFAULT_AGENT,
        task="finish",
        source_dir=create_source_repository(tmp_path / "source"),
    )

    with pytest.raises(RuntimeError, match="assembly failed"):
        await bootstrap(config)

    assert workspaces[0].state is LocalWorkspaceState.DESTROYED
    assert not workspaces[0].root.exists()
