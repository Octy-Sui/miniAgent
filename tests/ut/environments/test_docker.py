from datetime import UTC, datetime
from uuid import uuid4

import pytest

from miniagent.environments import (
    Command,
    ContainerUserPolicy,
    DockerOptions,
    DockerWorkspace,
    DockerWorkspaceError,
    DockerWorkspaceSpec,
    DockerWorkspaceState,
    ExecutionStatus,
    WorkspaceCheckpoint,
)
from tests.support.environments.docker import (
    ScriptedDockerBackend,
    docker_result,
    workspace_spec,
)


def test_workspace_spec_requires_absolute_container_path() -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        DockerWorkspaceSpec(
            run_id=uuid4(),
            options=DockerOptions(image="task-image", workdir="testbed"),
        )


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
