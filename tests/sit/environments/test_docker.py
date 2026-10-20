import os
from uuid import uuid4

import pytest

from miniagent.environments import (
    Command,
    DockerOptions,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
    ExecutionStatus,
)
from tests.support.environments.docker import UnusedArtifactStore


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
