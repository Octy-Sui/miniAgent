import asyncio
import base64
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.environments.contracts import Command
from miniagent.environments.docker_connection import resolve_docker_connection
from miniagent.environments.docker_exec import run_docker_exec_output
from miniagent.environments.docker_supervisor import encode_command, encode_guest_frame
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    MemoryExecutionObserver,
)
from miniagent.runtime.recording import RunPersistenceError
from tests.support.environments.docker_supervisor import receive


async def test_supervisor_separates_control_frames_from_command_output(
    supervisor_command,
):
    """TC-U11/U12: command text resembling control frames remains ordinary output."""
    process = await asyncio.create_subprocess_exec(
        *supervisor_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    attempt_id, execution_id = uuid4(), uuid4()
    expected_stdout = b'{"type":"completed","group_ended":true}\n'
    command = Command(
        argv=[
            "python",
            "-c",
            "import sys; sys.stdout.buffer.write(" + repr(expected_stdout) + ")",
        ],
        cwd="/",
        stdin="input",
    )
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        encode_command(
            command, attempt_id=attempt_id, execution_id=execution_id, timeout_seconds=5
        )
    )
    await process.stdin.drain()
    identified = await receive(process.stdout)
    assert identified["type"] == "guest_identified"
    guest = identified["body"]["guest"]
    assert guest["pid"] > 0 and guest["start_ticks"] > 0
    process.stdin.write(
        encode_guest_frame(attempt_id, execution_id, "permit_command", {})
    )
    await process.stdin.drain()
    output = bytearray()
    while True:
        frame = await receive(process.stdout)
        if frame["type"] == "completed":
            assert frame["body"]["group_ended"] and frame["body"]["issuer_ended"]
            break
        assert frame["type"] == "data"
        output.extend(base64.b64decode(frame["body"]["content"]))
    assert bytes(output) == expected_stdout
    assert await asyncio.wait_for(process.wait(), 5) == 0


async def test_supervisor_exit_does_not_hide_live_group(supervisor_command):
    """TC-U11-08: a successful leader with a live descendant cannot complete."""
    process = await asyncio.create_subprocess_exec(
        *supervisor_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    attempt_id, execution_id = uuid4(), uuid4()
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import signal; signal.pause()'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        encode_command(
            Command(argv=["python", "-c", code], cwd="/"),
            attempt_id=attempt_id,
            execution_id=execution_id,
            timeout_seconds=5,
        )
    )
    await process.stdin.drain()
    assert (await receive(process.stdout))["type"] == "guest_identified"
    process.stdin.write(
        encode_guest_frame(attempt_id, execution_id, "permit_command", {})
    )
    await process.stdin.drain()
    frames = []
    while True:
        frame = await receive(process.stdout)
        frames.append(frame)
        if frame["type"] == "error":
            break
    assert not any(frame["type"] == "completed" for frame in frames)
    assert await asyncio.wait_for(process.wait(), 5) != 0


async def test_supervisor_streams_large_stdin_and_binary_output(supervisor_command):
    process = await asyncio.create_subprocess_exec(
        *supervisor_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    attempt_id, execution_id = uuid4(), uuid4()
    code = "import sys; sys.stdout.buffer.write(b'x'*400000); sys.stdout.flush(); data=sys.stdin.buffer.read(); sys.stderr.buffer.write(b'err'); sys.stdout.buffer.write(str(len(data)).encode()+bytes([0,255]))"
    command = Command(argv=["python", "-c", code], cwd="/", stdin="λ" * 200000)
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        encode_command(
            command,
            attempt_id=attempt_id,
            execution_id=execution_id,
            timeout_seconds=10,
        )
    )
    await process.stdin.drain()
    assert (await receive(process.stdout))["type"] == "guest_identified"
    process.stdin.write(
        encode_guest_frame(attempt_id, execution_id, "permit_command", {})
    )
    await process.stdin.drain()
    streams = {1: bytearray(), 2: bytearray()}
    while True:
        frame = await receive(process.stdout)
        if frame["type"] == "completed":
            break
        assert frame["type"] == "data"
        streams[frame["body"]["stream"]].extend(
            base64.b64decode(frame["body"]["content"])
        )
    assert bytes(streams[1]) == b"x" * 400000 + b"400000\x00\xff"
    assert bytes(streams[2]) == b"err"
    assert await asyncio.wait_for(process.wait(), 5) == 0


async def test_killed_supervisor_leaves_no_completed_evidence(supervisor_command):
    process = await asyncio.create_subprocess_exec(
        *supervisor_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    attempt_id, execution_id = uuid4(), uuid4()
    code = "import os,signal; os.kill(os.getppid(),signal.SIGKILL); signal.pause()"
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(
        encode_command(
            Command(argv=["python", "-c", code], cwd="/"),
            attempt_id=attempt_id,
            execution_id=execution_id,
            timeout_seconds=5,
        )
    )
    await process.stdin.drain()
    frame = await receive(process.stdout)
    guest = frame["body"]["guest"]
    process.stdin.write(
        encode_guest_frame(attempt_id, execution_id, "permit_command", {})
    )
    await process.stdin.drain()
    assert await asyncio.wait_for(process.stdout.read(), 5) == b""
    assert await asyncio.wait_for(process.wait(), 5) != 0
    probe = [
        "python",
        "-c",
        "import os,sys; os.kill(int(sys.argv[1]),0)",
        str(guest["pid"]),
    ]
    if supervisor_command[0] == "docker":
        inspection = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            supervisor_command[3],
            *probe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(inspection.communicate(), 5)
        assert inspection.returncode == 0
    else:
        try:
            os.kill(guest["pid"], 0)
        finally:
            os.killpg(guest["pgid"], 9)


async def test_real_sdk_chain_preserves_binary_io_and_end_evidence(supervisor_command):
    if supervisor_command[0] != "docker":
        pytest.skip("Requires explicitly prepared Docker image for SDK chain")
    connection = await resolve_docker_connection(Path("docker"), dict(os.environ))
    observer = MemoryExecutionObserver()
    output = await run_docker_exec_output(
        Command(
            argv=[
                "python",
                "-c",
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'err')",
            ],
            cwd="/",
        ),
        connection=connection,
        container_id=supervisor_command[3],
        observer=observer,
        attempt_id=observer.attempt_id,
        deadline=monotonic() + 10,
        stdin_bytes=b"\x00\xffbinary",
        max_output_bytes=4,
    )
    assert output.stdout == b"\x00\xffbi" and output.result.stdout_truncated
    assert output.stderr == b"err"
    assert len(observer.evidence) == 2
    remote = next(
        identity
        for identity in observer.identities.values()
        if isinstance(identity, DockerExecIdentity)
    )
    assert remote.guest is not None
    assert observer.evidence[remote.execution_id].group_ended


async def test_guest_identity_commit_failure_never_releases_command(supervisor_command):
    if supervisor_command[0] != "docker":
        pytest.skip("Requires explicitly prepared Docker image for SDK chain")
    connection = await resolve_docker_connection(Path("docker"), dict(os.environ))

    class RejectGuestObserver(MemoryExecutionObserver):
        async def identified(self, identity, *, permission="observe", command=None):
            await super().identified(identity, permission=permission, command=command)
            if isinstance(identity, DockerExecIdentity) and identity.guest is not None:
                raise RunPersistenceError(
                    run_id=uuid4(), operation="identity", last_confirmed_sequence=1
                )

    observer = RejectGuestObserver()
    marker = "/tmp/miniagent-must-not-execute-" + uuid4().hex
    with pytest.raises(RunPersistenceError):
        await run_docker_exec_output(
            Command(argv=["touch", marker], cwd="/"),
            connection=connection,
            container_id=supervisor_command[3],
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    probe = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        supervisor_command[3],
        "test",
        "!",
        "-e",
        marker,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert await asyncio.wait_for(probe.wait(), 10) == 0
