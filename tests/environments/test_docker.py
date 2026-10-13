import asyncio
import os
import sys
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactStore, ArtifactWrite
from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments import (
    Command,
    ContainerUserPolicy,
    DockerOptions,
    DockerWorkspace,
    DockerWorkspaceBackend,
    DockerWorkspaceError,
    DockerWorkspaceSpec,
    DockerWorkspaceState,
    ExecutionStatus,
    ResourceLimits,
    WorkspaceCheckpoint,
)
from miniagent.environments.contracts import PatchExportCleanupError
from miniagent.environments.docker import DockerCommandResult


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
        print("diff --git a/changed.txt b/changed.txt")
"""
    )
    executable.chmod(0o755)
    (tmp_path / "mode").write_text("ok")
    backend = DockerWorkspaceBackend(
        artifact_store=UnusedArtifactStore(), docker_executable=str(executable)
    )
    return DockerWorkspace(
        backend=backend,
        spec=workspace_spec(),
        _workspace_id="test",
        container_id="agent-container",
        image_id="image",
        base_revision="actual-base",
    )


async def test_export_patch_shares_budget_with_cleanup(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("hang")
    started = monotonic()
    with pytest.raises(PatchExportCleanupError, match="cleanup|Cleanup"):
        await export_workspace.export_patch(
            timeout_seconds=3.0, cleanup_timeout_seconds=0.3
        )
    assert monotonic() - started < 6.0
    pid = int((tmp_path / "cleanup-started").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancel_during_index_cleanup_preserves_cancellation(
    export_workspace, tmp_path
):
    (tmp_path / "mode").write_text("hang")
    exporting = asyncio.create_task(
        export_workspace.export_patch(timeout_seconds=3.0, cleanup_timeout_seconds=0.3)
    )
    while not (tmp_path / "cleanup-started").exists() and not exporting.done():
        await asyncio.sleep(0.01)
    exporting.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await exporting
    assert "index" in " ".join(caught.value.__notes__)


async def test_cleanup_failure_rejects_generated_diff(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("error")
    with pytest.raises(PatchExportCleanupError, match="cleanup|Cleanup"):
        await export_workspace.export_patch(
            timeout_seconds=2.0, cleanup_timeout_seconds=0.5
        )


class UnusedArtifactStore(ArtifactStore):
    async def put(self, request: ArtifactWrite, content: bytes) -> ArtifactRef:
        raise AssertionError("Artifact store should not be used")

    async def read(self, ref: ArtifactRef) -> bytes:
        raise AssertionError("Artifact store should not be used")


class ScriptedDockerBackend(DockerWorkspaceBackend):
    def __init__(self, responses: list[DockerCommandResult | BaseException]) -> None:
        super().__init__(artifact_store=UnusedArtifactStore())
        self.responses = responses
        self.commands: list[list[str]] = []

    async def _run(
        self,
        argv: list[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> DockerCommandResult:
        self.commands.append(argv)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def docker_result(
    stdout: bytes = b"",
    *,
    exit_code: int = 0,
) -> DockerCommandResult:
    return DockerCommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=b"",
        duration_ms=1,
    )


def workspace_spec() -> DockerWorkspaceSpec:
    return DockerWorkspaceSpec(
        run_id=uuid4(),
        options=DockerOptions(
            image="swebench/task:latest",
            workdir="/testbed",
            limits=ResourceLimits(cpu_count=1.5, memory_mb=768, pids=64),
        ),
    )


def test_workspace_spec_requires_absolute_container_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        DockerWorkspaceSpec(
            run_id=uuid4(),
            options=DockerOptions(image="task-image", workdir="testbed"),
        )


async def test_docker_command_output_is_bounded_while_stream_is_drained() -> None:
    backend = DockerWorkspaceBackend(
        artifact_store=UnusedArtifactStore(),
        docker_executable="/bin/sh",
        max_output_bytes=4,
    )

    result = await backend._run(["-c", "printf 123456"])

    assert result.stdout == b"1234"
    assert result.stdout_truncated


async def test_backend_creates_run_container_with_resource_limits() -> None:
    backend = ScriptedDockerBackend(
        [
            docker_result(b"sha256:image\n"),
            docker_result(b"container-id\n"),
            docker_result(),
            docker_result(),
            docker_result(b"abc123\n"),
            docker_result(),
        ]
    )

    workspace = await backend.create(workspace_spec())

    create_command = backend.commands[1]
    assert workspace.image_id == "sha256:image"
    assert workspace.container_id == "container-id"
    cap_drop = create_command.index("--cap-drop")
    assert create_command[cap_drop : cap_drop + 2] == ["--cap-drop", "ALL"]
    assert "no-new-privileges:true" in create_command
    assert create_command[create_command.index("--pids-limit") + 1] == "64"
    assert create_command[create_command.index("--cpus") + 1] == "1.5"
    assert create_command[create_command.index("--memory") + 1] == "768m"
    assert create_command[create_command.index("--memory-swap") + 1] == "768m"
    assert create_command[create_command.index("--network") + 1] == "none"
    assert "--user" not in create_command
    assert workspace.base_revision == "abc123"


async def test_backend_rejects_root_when_non_root_is_required() -> None:
    backend = ScriptedDockerBackend(
        [
            docker_result(b"sha256:image\n"),
            docker_result(b"container-id\n"),
            docker_result(),
            docker_result(),
            docker_result(b"0\n"),
            docker_result(),
        ]
    )
    spec = workspace_spec()
    spec = spec.model_copy(
        update={
            "options": spec.options.model_copy(
                update={"user_policy": ContainerUserPolicy.REQUIRE_NON_ROOT}
            )
        }
    )

    with pytest.raises(DockerWorkspaceError, match="runs as root"):
        await backend.create(spec)

    assert backend.commands[-1][:3] == ["container", "rm", "--force"]


async def test_recovery_requires_checkpoint_image_baseline() -> None:
    backend = ScriptedDockerBackend([])
    spec = workspace_spec()

    with pytest.raises(ValueError, match="has no image baseline"):
        await backend.recover(
            spec,
            WorkspaceCheckpoint(
                checkpoint_id="checkpoint-id",
                run_id=spec.run_id,
                workspace_id="workspace-id",
                revision="revision",
                created_at=datetime.now(UTC),
            ),
        )


async def test_command_timeout_breaks_and_kills_workspace() -> None:
    backend = ScriptedDockerBackend([TimeoutError(), docker_result()])
    spec = workspace_spec()

    workspace = DockerWorkspace(
        backend=backend,
        spec=spec,
        _workspace_id="workspace-id",
        container_id="container-id",
        image_id="sha256:image",
        base_revision="abc123",
    )

    assert (
        await workspace.execute(Command(argv=["pytest"]))
    ).status is ExecutionStatus.TIMEOUT
    assert workspace.state is DockerWorkspaceState.BROKEN
    assert backend.commands[-1] == ["container", "kill", "container-id"]


async def test_export_patch_compares_current_files_to_initial_revision() -> None:
    backend = ScriptedDockerBackend(
        [
            docker_result(),
            docker_result(),
            docker_result(b"diff --git a/new.py b/new.py\n"),
            docker_result(b"new.py\0"),
            docker_result(),
        ]
    )
    workspace = DockerWorkspace(
        backend=backend,
        spec=workspace_spec(),
        _workspace_id="workspace-id",
        container_id="container-id",
        image_id="sha256:image",
        base_revision="abc123",
    )

    patch = await workspace.export_patch()

    assert patch.base_revision == "abc123"
    assert patch.files == ["new.py"]
    assert patch.content == "diff --git a/new.py b/new.py\n"
    assert "read-tree" in backend.commands[0]
    assert "abc123" in backend.commands[0]
    assert ["add", "-A", "--", "."] == backend.commands[1][-4:]


async def test_destroy_is_idempotent() -> None:
    backend = ScriptedDockerBackend([docker_result()])
    spec = workspace_spec()

    workspace = DockerWorkspace(
        backend=backend,
        spec=spec,
        _workspace_id="workspace-id",
        container_id="container-id",
        image_id="sha256:image",
        base_revision="abc123",
    )

    await workspace.destroy()
    await workspace.destroy()

    assert workspace.state is DockerWorkspaceState.DESTROYED
    assert backend.commands == [["container", "rm", "--force", "container-id"]]


@pytest.mark.skipif(
    "MINIAGENT_DOCKER_IMAGE" not in os.environ,
    reason="MINIAGENT_DOCKER_IMAGE is not configured",
)
async def test_docker_workspace_with_task_image() -> None:
    backend = DockerWorkspaceBackend(artifact_store=UnusedArtifactStore())
    workspace = await backend.create(
        DockerWorkspaceSpec(
            run_id=uuid4(),
            options=DockerOptions(
                image=os.environ["MINIAGENT_DOCKER_IMAGE"],
                workdir="/testbed",
            ),
        )
    )

    try:
        result = await workspace.execute(
            Command(
                argv=[
                    "/bin/sh",
                    "-c",
                    "printf workspace-probe > miniagent_workspace_probe.txt",
                ]
            )
        )
        patch = await workspace.export_patch()

        assert result.status is ExecutionStatus.SUCCESS
        assert "miniagent_workspace_probe.txt" in patch.files
        assert "workspace-probe" in patch.content
    finally:
        await workspace.destroy()
