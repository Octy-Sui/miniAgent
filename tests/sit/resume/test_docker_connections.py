import asyncio
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from miniagent.artifacts import ArtifactRef, FileArtifactStore
from miniagent.environments.config import DockerOptions, DockerWorkspaceSpec
from miniagent.environments.contracts import Command
from miniagent.environments.docker import DockerWorkspaceBackend
from miniagent.environments.docker_connection import (
    FrozenDockerConnection,
    bind_docker_connection,
    check_daemon,
    docker_argv,
)
from miniagent.environments.process import identify_host_process
from miniagent.evaluation.swebench_docker import (
    SWEbenchAttemptState,
    cleanup_attempt_resources,
    inspect_attempt_resources,
)


class ConnectionsManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workspace: FrozenDockerConnection
    evaluation: FrozenDockerConnection
    ambient: FrozenDockerConnection
    workspace_image: str
    evaluation_image: str


async def command(connection, *args):
    process = await asyncio.create_subprocess_exec(
        *docker_argv(Path("docker"), connection, list(args)),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def test_workspace_and_evaluator_use_their_own_daemons(tmp_path, monkeypatch):
    """U21-05: actual A/B boundary and strict evaluation cleanup; full harness journey separately DEFER."""
    path = os.environ.get("MINIAGENT_RESUME_CONNECTIONS_MANIFEST")
    if not path:
        pytest.skip(
            "DEFER: three independently prepared daemon connections unavailable"
        )
    manifest = ConnectionsManifest.model_validate_json(Path(path).read_bytes())
    assert (
        len(
            {
                c.daemon_id
                for c in (manifest.workspace, manifest.evaluation, manifest.ambient)
            }
        )
        == 3
    )
    a, b, c = [
        bind_docker_connection(item, dict(os.environ))
        for item in (manifest.workspace, manifest.evaluation, manifest.ambient)
    ]
    for connection in (a, b, c):
        await check_daemon(Path("docker"), connection)
    monkeypatch.setenv("DOCKER_HOST", c.endpoint)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"), connection=a
    )
    workspace = await backend.create(
        DockerWorkspaceSpec(
            run_id=uuid4(),
            options=DockerOptions(image=manifest.workspace_image, workdir="/testbed"),
        )
    )
    run_id, attempt_id = uuid4(), uuid4()
    harness_id = f"{run_id.hex}-{attempt_id.hex}"
    name = f"sweb.eval.resume-acceptance.{harness_id}"
    owned = await command(
        b,
        "run",
        "--detach",
        "--pull=never",
        "--network=none",
        "--label",
        "miniagent.resume-test=" + harness_id,
        "--name",
        name,
        "--entrypoint",
        "sleep",
        manifest.evaluation_image,
        "infinity",
    )
    neighbor = await command(
        b,
        "run",
        "--detach",
        "--pull=never",
        "--network=none",
        "--label",
        "miniagent.resume-test=" + harness_id,
        "--name",
        name + ".neighbor",
        "--entrypoint",
        "sleep",
        manifest.evaluation_image,
        "infinity",
    )
    try:
        state = SWEbenchAttemptState(
            attempt_id=attempt_id,
            harness_run_id=harness_id,
            instance_id="resume-acceptance",
            task_digest="a" * 64,
            patch_ref=ArtifactRef(artifact_id=uuid4(), uri="acceptance://unused"),
            patch_sha256="b" * 64,
            directory=tmp_path / run_id.hex / attempt_id.hex,
            resource_instance_id="resume-acceptance",
            resource_harness_run_id=harness_id,
        )
        host = await asyncio.create_subprocess_exec(
            "sleep", "0.1", start_new_session=True
        )
        identity = identify_host_process(
            host.pid, attempt_id=attempt_id, execution_id=uuid4(), role="harness"
        )
        await host.wait()
        facts = await inspect_attempt_resources(state, b, host_executions=(identity,))
        assert facts.host_ended and not facts.errors
        assert [item.container_id for item in facts.containers] == [owned]
        await cleanup_attempt_resources(facts, deadline=monotonic() + 10)
        assert (
            await command(a, "inspect", "--format", "{{.Id}}", workspace.container_id)
            == workspace.container_id
        )
        assert await command(b, "inspect", "--format", "{{.Id}}", neighbor) == neighbor
        (tmp_path / "connections-evidence.json").write_text(
            json.dumps(
                {
                    "workspace_operations_daemon": a.daemon_id,
                    "evaluation_cleanup_daemon": b.daemon_id,
                    "ambient_business_operations": [],
                    "agent_retained": workspace.container_id,
                    "neighbor_retained": neighbor,
                },
                indent=2,
            )
        )
    finally:
        # Full IDs were created by this test, with matching ownership labels.
        for container in (owned, neighbor):
            process = await asyncio.create_subprocess_exec(
                *docker_argv(
                    Path("docker"),
                    b,
                    [
                        "inspect",
                        "--format",
                        '{{index .Config.Labels "miniagent.resume-test"}}',
                        container,
                    ],
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                assert stdout.decode().strip() == harness_id
                await command(b, "rm", "--force", container)
        await workspace.destroy()


async def test_frozen_tls_connection_survives_resume(tmp_path, monkeypatch):
    """U21-06: actual verified TLS CLI and SDK handshake, no embedded certificate bytes."""
    path = os.environ.get("MINIAGENT_RESUME_TLS_MANIFEST")
    image = os.environ.get("MINIAGENT_RESUME_TLS_IMAGE")
    if not path or not image:
        pytest.skip(
            "DEFER: independently prepared verified TLS daemon/image unavailable"
        )
    frozen = FrozenDockerConnection.model_validate_json(Path(path).read_bytes())
    assert frozen.tls_verify and frozen.cert_path is not None
    connection = bind_docker_connection(frozen, dict(os.environ))
    await check_daemon(Path("docker"), connection)
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"), connection=connection
    )
    spec = DockerWorkspaceSpec(
        run_id=uuid4(), options=DockerOptions(image=image, workdir="/testbed")
    )
    workspace = await backend.create(spec)
    try:
        attachment = workspace.describe_attachment()
        encoded = attachment.model_dump_json()
        assert "BEGIN CERTIFICATE" not in encoded and "PRIVATE KEY" not in encoded
        monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
        monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
        restored = DockerWorkspaceBackend(
            artifact_store=FileArtifactStore(tmp_path / "artifacts")
        )
        attached = await restored.attach(spec, attachment)
        assert attached.container_id == workspace.container_id
        assert (
            await attached.execute(Command(argv=["printf", "verified-tls"]))
        ).stdout == "verified-tls"
        (tmp_path / "tls-evidence.json").write_text(
            json.dumps(
                {
                    "daemon_id": frozen.daemon_id,
                    "verified_tls": True,
                    "full_container_id": workspace.container_id,
                    "cert_path_reference": str(frozen.cert_path),
                },
                indent=2,
            )
        )
    finally:
        await workspace.destroy()
