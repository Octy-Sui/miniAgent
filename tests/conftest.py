import sys
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    DockerOptions,
    DockerWorkspace,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
)


@pytest.fixture
def export_workspace(tmp_path):
    executable = tmp_path / "docker-export"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
with (root / "export-calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if "rm" in args:
    (root / "cleanup-started").write_text(str(os.getpid()))
    mode = (root / "mode").read_text()
    if mode == "hang":
        time.sleep(30)
    elif mode == "error":
        sys.exit(1)
elif "diff" in args:
    if "--name-only" in args:
        sys.stdout.buffer.write(b"changed.txt\\0")
    else:
        sys.stdout.write(" " if (root / "mode").read_text() == "whitespace" else "diff --git a/changed.txt b/changed.txt\\n")
"""
    )
    executable.chmod(0o755)
    (tmp_path / "mode").write_text("ok")
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "unused-artifacts"),
        docker_executable=str(executable),
    )
    return DockerWorkspace(
        backend=backend,
        spec=DockerWorkspaceSpec(
            run_id=uuid4(), options=DockerOptions(image="synthetic", workdir="/testbed")
        ),
        _workspace_id="test",
        container_id="agent-container",
        image_id="image",
        base_revision="actual-base",
    )
