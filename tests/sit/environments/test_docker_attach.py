import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.config import DockerOptions, DockerWorkspaceSpec
from miniagent.environments.contracts import Command
from miniagent.environments.docker import DockerWorkspaceBackend
from miniagent.environments.docker_connection import (
    resolve_docker_connection,
)


async def test_real_running_and_exited_container_retains_original_layer(tmp_path):
    image = os.environ.get("MINIAGENT_RESUME_DOCKER_IMAGE")
    if image is None:
        pytest.skip("DEFER: requires an explicitly prepared Docker image; no pull")
    connection = await resolve_docker_connection(Path("docker"), dict(os.environ))
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"), connection=connection
    )
    spec = DockerWorkspaceSpec(
        run_id=uuid4(), options=DockerOptions(image=image, workdir="/testbed")
    )
    workspace = await backend.create(spec)
    try:
        attachment = workspace.describe_attachment()
        assert (
            await backend.inspect_attachment(attachment)
        ).container_status == "running"
        marker = "miniagent-retained-" + uuid4().hex
        assert (await workspace.execute(Command(argv=["touch", marker]))).exit_code == 0
        stop = await asyncio.create_subprocess_exec(
            "docker",
            "--host",
            connection.endpoint,
            "stop",
            "--time",
            "1",
            workspace.container_id,
            stdout=asyncio.subprocess.DEVNULL,
        )
        assert await asyncio.wait_for(stop.wait(), 10) == 0
        facts = await backend.inspect_attachment(attachment)
        assert facts.container_status == "exited" and facts.identity_matches
        attached = await backend.attach(spec, attachment)
        assert attached.container_id == workspace.container_id
        assert (
            await attached.execute(Command(argv=["test", "-e", marker]))
        ).exit_code == 0
    finally:
        await workspace.destroy()
