import asyncio
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.environments import docker_supervisor
from miniagent.environments.contracts import Command
from miniagent.environments.docker_exec import (
    inspect_guest_execution,
    run_docker_exec_output,
)
from miniagent.environments.docker_supervisor import encode_command, encode_guest_frame
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    MemoryExecutionObserver,
)
from tests.support.environments.docker_supervisor import receive

pytestmark = pytest.mark.resume_docker


async def test_real_helper_and_guest_birth_identity_is_recorded(
    docker_workspace, tmp_path
):
    _, workspace, _, connection = docker_workspace

    class Observer(MemoryExecutionObserver):
        async def identified(self, identity, *, permission="observe"):
            await super().identified(identity, permission=permission)
            with (tmp_path / "permissions.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "time": monotonic(),
                            "permission": permission,
                            "identity": identity.model_dump(mode="json"),
                        }
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())

    observer = Observer()
    result = await run_docker_exec_output(
        Command(argv=["printf", "binary-ok"]),
        connection=connection,
        container_id=workspace.container_id,
        observer=observer,
        attempt_id=observer.attempt_id,
        deadline=monotonic() + 10,
    )
    assert result.stdout == b"binary-ok"
    identities = list(observer.identities.values())
    remote = next(i for i in identities if isinstance(i, DockerExecIdentity))
    assert remote.guest and remote.guest.start_ticks > 0
    assert remote.exec_id and remote.daemon_id == connection.daemon_id
    assert len(observer.evidence) == 2 and all(
        e.group_ended for e in observer.evidence.values()
    )
    assert (await inspect_guest_execution(remote, workspace)).outcome == "ended"


async def test_dead_supervisor_with_live_group_is_busy(docker_workspace, tmp_path):
    _, workspace, _, connection = docker_workspace
    source = Path(docker_supervisor.__file__).read_text()
    process = await asyncio.create_subprocess_exec(
        "docker",
        "--host",
        connection.endpoint,
        "exec",
        "--interactive",
        workspace.container_id,
        "python",
        "-c",
        source,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    attempt_id, execution_id = uuid4(), uuid4()
    assert process.stdin and process.stdout
    process.stdin.write(
        encode_command(
            Command(
                argv=[
                    "python",
                    "-c",
                    "import os,signal; os.kill(os.getppid(),signal.SIGKILL); signal.pause()",
                ],
                cwd="/",
            ),
            attempt_id=attempt_id,
            execution_id=execution_id,
            timeout_seconds=5,
        )
    )
    await process.stdin.drain()
    identified = await receive(process.stdout)
    assert identified["type"] == "guest_identified"
    guest = identified["body"]["guest"]
    (tmp_path / "live-guest.json").write_text(json.dumps(identified, indent=2))
    process.stdin.write(
        encode_guest_frame(attempt_id, execution_id, "permit_command", {})
    )
    await process.stdin.drain()
    stdout = await asyncio.wait_for(process.stdout.read(), 10)
    assert await asyncio.wait_for(process.wait(), 10) != 0
    assert process.returncode != 0 and b"completed" not in stdout
    identity = DockerExecIdentity(
        execution_id=execution_id,
        attempt_id=attempt_id,
        daemon_id=connection.daemon_id,
        container_id=workspace.container_id,
        exec_id="externally-killed-supervisor",
        helper_execution_id=uuid4(),
        start_permitted=True,
        started_observed=True,
        guest=guest,
    )
    evidence = await inspect_guest_execution(identity, workspace)
    assert evidence.outcome == "active" and not evidence.group_ended
    (tmp_path / "guest-inspection.json").write_text(evidence.model_dump_json(indent=2))
