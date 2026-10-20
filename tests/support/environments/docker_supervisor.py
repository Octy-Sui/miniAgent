import asyncio
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.environments import docker_supervisor


@pytest.fixture
def supervisor_command():
    image = os.environ.get("MINIAGENT_RESUME_DOCKER_IMAGE")
    if sys.platform == "linux" and image is None:
        yield [sys.executable, "-m", "miniagent.environments.docker_supervisor"]
        return
    if image is None:
        pytest.skip(
            "DEFER: Linux supervisor requires Linux or an explicitly prepared Docker image"
        )
    name = "miniagent-resume-supervisor-" + uuid4().hex
    source = Path(docker_supervisor.__file__).read_text()
    subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--pull=never",
            "--network=none",
            "--name",
            name,
            "--label",
            "miniagent.resume-test=" + name,
            "--entrypoint",
            "sleep",
            image,
            "infinity",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    yield ["docker", "exec", "--interactive", name, "python", "-c", source]
    inspected = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "miniagent.resume-test"}}',
            name,
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if inspected.returncode == 0:
        assert inspected.stdout.strip() == name
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=True,
            capture_output=True,
            timeout=10,
        )


async def receive(reader):
    size = struct.unpack(">I", await asyncio.wait_for(reader.readexactly(4), 5))[0]
    assert size <= 262144
    return json.loads(await asyncio.wait_for(reader.readexactly(size), 5))
