import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
)


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
