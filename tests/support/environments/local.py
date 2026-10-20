import subprocess
from pathlib import Path

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    LocalWorkspaceBackend,
)


def run_git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def source_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    run_git(repository, "init", "--quiet")
    (repository / "tracked.txt").write_text("committed\n", encoding="utf-8")
    run_git(repository, "add", "tracked.txt")
    run_git(
        repository,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "--quiet",
        "-m",
        "initial",
    )
    return repository


def local_backend(tmp_path: Path) -> LocalWorkspaceBackend:
    return LocalWorkspaceBackend(
        root=tmp_path / "workspaces",
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    )
