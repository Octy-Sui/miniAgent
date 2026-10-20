import json
import sys
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.attachment import DockerAttachment
from miniagent.environments.docker import DockerWorkspaceBackend
from miniagent.environments.docker_connection import (
    FrozenDockerConnection,
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
