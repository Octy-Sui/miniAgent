import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.attachment import DockerAttachment
from miniagent.environments.config import DockerOptions, DockerWorkspaceSpec
from miniagent.environments.contracts import Command
from miniagent.environments.docker import DockerWorkspaceBackend
from miniagent.environments.docker_connection import (
    FrozenDockerConnection,
    resolve_docker_connection,
)


@pytest.fixture
def retained(tmp_path):
    run_id = uuid4()
    attachment = DockerAttachment(
        run_id=run_id,
        workspace_id="original",
        base_revision="a" * 40,
        container_id="b" * 64,
        image_id="sha256:original",
        workdir="/testbed",
        labels={
            "miniagent.managed": "true",
            "miniagent.run_id": str(run_id),
            "miniagent.workspace_id": "original",
        },
        security_options=("no-new-privileges:true",),
        resource_limits={
            "NanoCpus": 1000000000,
            "Memory": 1073741824,
            "MemorySwap": 1073741824,
            "PidsLimit": 256,
            "CapDrop": ["ALL"],
            "NetworkMode": "none",
            "Init": True,
        },
        connection=FrozenDockerConnection(
            source_context="original",
            endpoint="tcp://127.0.0.1:2375",
            cert_path=None,
            tls_verify=False,
            daemon_id="original-daemon",
        ),
    )
    document = {
        "Id": attachment.container_id,
        "Image": attachment.image_id,
        "Config": {"WorkingDir": attachment.workdir, "Labels": attachment.labels},
        "HostConfig": {
            **attachment.resource_limits,
            "SecurityOpt": list(attachment.security_options),
        },
        "State": {"Status": "exited"},
    }
    (tmp_path / "inspect.json").write_text(json.dumps(document))
    executable = tmp_path / "docker-inspect"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, sys
from pathlib import Path
root = Path(__file__).parent
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if "info" in sys.argv:
    print("original-daemon")
elif "inspect" in sys.argv:
    print("[" + (root / "inspect.json").read_text() + "]")
else:
    sys.exit(13)
"""
    )
    executable.chmod(0o755)
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        docker_executable=str(executable),
    )
    return backend, attachment, document


async def test_stopped_inspection_defers_git_without_start(retained, tmp_path):
    backend, attachment, _ = retained
    facts = await backend.inspect_attachment(attachment)
    assert facts.identity_matches and facts.container_status == "exited"
    assert facts.live_checks == ("workdir", "git_baseline", "guest_execution_groups")
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert all("exec" not in call and "start" not in call for call in calls)
    assert calls[-1][-1] == attachment.container_id
    assert all(call[:2] == ["--host", attachment.connection.endpoint] for call in calls)


@pytest.mark.parametrize(
    ("status",), [("running",), ("paused",), ("restarting",), ("removing",), ("dead",)]
)
async def test_container_status_is_checked_without_repairs(retained, tmp_path, status):
    backend, attachment, document = retained
    document["State"]["Status"] = status
    (tmp_path / "inspect.json").write_text(json.dumps(document))
    facts = await backend.inspect_attachment(attachment)
    assert bool(facts.errors) == (status != "running")


@pytest.mark.parametrize(
    ("field",),
    [
        ("Id",),
        ("Image",),
        ("WorkingDir",),
        ("Labels",),
        ("Memory",),
        ("SecurityOpt",),
        ("CapDrop",),
    ],
)
async def test_single_identity_or_security_mismatch_is_rejected(
    retained, tmp_path, field
):
    backend, attachment, document = retained
    if field in ("Id", "Image"):
        document[field] = "replacement"
    elif field in ("WorkingDir", "Labels"):
        document["Config"][field] = {} if field == "Labels" else "/replacement"
    else:
        document["HostConfig"][field] = 0
    (tmp_path / "inspect.json").write_text(json.dumps(document))
    facts = await backend.inspect_attachment(attachment)
    assert not facts.identity_matches and facts.errors


async def test_attach_uses_full_original_id_and_frozen_connection(
    retained, tmp_path, export_engine
):
    backend, attachment, _ = retained
    executable = Path(backend.docker_executable)
    source = executable.read_text().replace("original-daemon", "export-test")
    source = source.replace(
        "else:\n    sys.exit(13)",
        'elif "--show-toplevel" in sys.argv:\n    print("/testbed")\nelif "start" in sys.argv or "/bin/sh" in sys.argv or "cat-file" in sys.argv:\n    pass\nelse:\n    sys.exit(13)',
    )
    executable.write_text(source)
    connection = export_engine(executable)
    attachment = attachment.model_copy(
        update={
            "connection": FrozenDockerConnection(
                source_context="frozen",
                endpoint=connection.endpoint,
                daemon_id=connection.daemon_id,
                cert_path=None,
                tls_verify=False,
            )
        }
    )
    spec = DockerWorkspaceSpec(
        run_id=attachment.run_id,
        options=DockerOptions(image="unused", workdir="/testbed"),
    )
    attached = await backend.attach(spec, attachment)
    assert attached.container_id == attachment.container_id
    assert attached.base_revision == attachment.base_revision
    assert attached.describe_attachment().connection.endpoint == connection.endpoint
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert sum("start" in call for call in calls) == 1
    assert all(
        "create" not in call and "pull" not in call and "rm" not in call
        for call in calls
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
