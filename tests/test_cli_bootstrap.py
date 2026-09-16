import subprocess
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from miniagent.agents.default import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.bootstrap import (
    BootstrapConfig,
    WorkspaceConfig,
    bootstrap,
    workspace,
)
from miniagent.environments import (
    LocalWorkspace,
    LocalWorkspaceState,
    WorkspaceKind,
)
from miniagent.models import Settings


def create_source_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "-C", str(path), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    (path / "source.txt").write_text("source\n", encoding="utf-8")
    return path


async def test_workspace_context_destroys_workspace_after_error(
    tmp_path: Path,
) -> None:
    settings = Settings(
        openai_api_key="test",
        openai_base_url="https://example.com",
        local_workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "artifacts",
    )
    created_workspace = None
    config = WorkspaceConfig(
        kind=WorkspaceKind.LOCAL,
        run_id=uuid4(),
        source_dir=create_source_repository(tmp_path / "source"),
        settings=settings,
    )

    with pytest.raises(RuntimeError, match="run failed"):
        async with workspace(config) as active_workspace:
            created_workspace = active_workspace
            raise RuntimeError("run failed")

    assert created_workspace is not None
    local_workspace = cast(LocalWorkspace, created_workspace)
    assert local_workspace.state is LocalWorkspaceState.DESTROYED
    assert not local_workspace.root.exists()


async def test_bootstrap_persists_workspace_patch_before_destroy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv(
        "MODEL_PRICING_FILE",
        str(Path.cwd() / "config" / "model_pricing.toml"),
    )
    config = BootstrapConfig(
        agent=DEFAULT_AGENT,
        task="create a result file",
        source_dir=create_source_repository(tmp_path / "source"),
    )

    async with bootstrap(config) as agent:
        result = await agent.tools["shell"].execute(
            {"command": "printf result > result.txt"},
            tool_call_id="write-result",
        )
        assert result.error is None

    assert agent.patch_artifact is not None
    patch = await FileArtifactStore(artifact_root).read(agent.patch_artifact)
    assert b"result.txt" in patch
    assert not (config.source_dir / "result.txt").exists()
