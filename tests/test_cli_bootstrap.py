import subprocess
from pathlib import Path
from typing import cast

import pytest

from miniagent.agents.default import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.bootstrap import (
    BootstrapConfig,
    bootstrap,
)
from miniagent.environments import (
    LocalWorkspace,
    LocalWorkspaceState,
)
from miniagent.models.model import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    Usage,
)
from miniagent.providers import ModelProvider
from miniagent.runtime import CancellationToken, RunStatus
from miniagent.tools.shell import ShellTool


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
            message=Message(role="assistant", content="finished"),
            finish_reason=self.finish_reason,
            usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
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


async def test_bootstrap_persists_patch_and_retains_workspace_without_run_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    configure_settings(tmp_path, monkeypatch)
    config = BootstrapConfig(
        agent=DEFAULT_AGENT,
        task="create a result file",
        source_dir=create_source_repository(tmp_path / "source"),
    )

    async with bootstrap(config) as agent:
        workspace = cast(ShellTool, agent.tools["shell"]).workspace
        result = await agent.tools["shell"].execute(
            {"command": "printf result > result.txt"},
            tool_call_id="write-result",
        )
        assert result.error is None

    assert agent.patch_artifact is not None
    patch = await FileArtifactStore(artifact_root).read(agent.patch_artifact)
    assert b"result.txt" in patch
    assert not (config.source_dir / "result.txt").exists()
    assert cast(LocalWorkspace, workspace).state is LocalWorkspaceState.ACTIVE
    await workspace.destroy()


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

    async with bootstrap(config) as agent:
        workspace = cast(ShellTool, agent.tools["shell"]).workspace
        result = await agent.run(CancellationToken())

    local_workspace = cast(LocalWorkspace, workspace)
    assert result.status is expected_status
    assert (local_workspace.state is LocalWorkspaceState.DESTROYED) is (
        expected_destroyed
    )
    assert local_workspace.root.exists() is not expected_destroyed
    if not expected_destroyed:
        await local_workspace.destroy()
