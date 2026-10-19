import asyncio
import base64
import socket
import struct
import sys
from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from pydantic import JsonValue

from miniagent.environments.contracts import Command, ExecutionResult, ExecutionStatus
from miniagent.environments.docker_connection import DockerConnection
from miniagent.environments.docker_exec_helper import (
    MAX_FRAME_BYTES,
    Frame,
    HelperRequest,
    encode_frame,
)
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    ExecutionEvidence,
    ExecutionObserver,
    HostProcessIdentity,
    ManagedExecutionError,
)
from miniagent.environments.process import (
    identify_host_process,
    inspect_host_execution,
    reap_process,
    register_host_process,
)


@dataclass(slots=True)
class _Helper:
    process: asyncio.subprocess.Process
    channel: socket.socket
    identity: HostProcessIdentity
    attempt_id: UUID
    execution_id: UUID
    deadline: float

    async def send(self, kind: str, body: dict[str, JsonValue] | None = None) -> None:
        if monotonic() >= self.deadline:
            raise ManagedExecutionError(
                "Docker helper deadline exhausted before permission"
            )
        async with asyncio.timeout_at(self.deadline):
            await asyncio.get_running_loop().sock_sendall(
                self.channel,
                encode_frame(
                    Frame(
                        attempt_id=self.attempt_id,
                        execution_id=self.execution_id,
                        type=kind,
                        body=body or {},
                    )
                ),
            )

    async def receive(self) -> Frame:
        async def exact(count: int) -> bytes:
            result = bytearray()
            while len(result) < count:
                chunk = await asyncio.get_running_loop().sock_recv(
                    self.channel, count - len(result)
                )
                if not chunk:
                    raise ManagedExecutionError(
                        "Docker helper ended before confirmation"
                    )
                result.extend(chunk)
            return bytes(result)

        async with asyncio.timeout_at(self.deadline):
            length = struct.unpack(">I", await exact(4))[0]
            if length > MAX_FRAME_BYTES:
                raise ManagedExecutionError(
                    "Docker helper frame exceeds protocol limit"
                )
            frame = Frame.model_validate_json(await exact(length))
        if (
            frame.attempt_id != self.attempt_id
            or frame.execution_id != self.execution_id
        ):
            raise ManagedExecutionError("Docker helper frame scope mismatch")
        return frame

    async def close(self) -> None:
        self.channel.close()
        diagnostics = await reap_process(self.process, [], deadline=self.deadline)
        if diagnostics:
            raise ManagedExecutionError("Docker helper whole group end is unconfirmed")


async def _launch(request: HelperRequest, *, attempt_id: UUID) -> _Helper:
    if monotonic() >= request.deadline:
        raise ManagedExecutionError("Docker helper deadline exhausted before launch")
    parent, child = socket.socketpair()
    parent.setblocking(False)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "miniagent.environments.docker_exec_helper",
            str(child.fileno()),
            pass_fds=(child.fileno(),),
            start_new_session=True,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        execution_id = uuid4()
        identity = identify_host_process(
            process.pid,
            attempt_id=attempt_id,
            execution_id=execution_id,
            role="docker_helper",
        )
        register_host_process(process, identity)
        helper = _Helper(
            process, parent, identity, attempt_id, execution_id, request.deadline
        )
        await helper.send("request", request.model_dump(mode="json"))
        return helper
    except BaseException:
        parent.close()
        if process is not None:
            await reap_process(process, [], deadline=request.deadline)
        raise
    finally:
        child.close()


def _request(
    connection: DockerConnection,
    *,
    container_id: str,
    command: Command | None,
    deadline: float,
    exec_id: str | None = None,
) -> HelperRequest:
    return HelperRequest(
        mode="inspect" if exec_id is not None else "execute",
        endpoint=connection.endpoint,
        cert_path=connection.cert_path,
        tls_verify=connection.tls_verify,
        container_id=container_id,
        command=command,
        exec_id=exec_id,
        deadline=deadline,
    )


async def run_docker_exec(
    command: Command,
    *,
    connection: DockerConnection,
    container_id: str,
    observer: ExecutionObserver,
    attempt_id: UUID,
    deadline: float,
    max_output_bytes: int = 1024 * 1024,
) -> ExecutionResult:
    if observer.attempt_id != attempt_id:
        raise ManagedExecutionError("Docker observer attempt mismatch")
    if max_output_bytes < 1:
        raise ValueError("Docker output limit must be positive")
    started = monotonic()
    reserve = min(1.0, max(0.0, deadline - started) / 4)
    helper = await _launch(
        _request(
            connection,
            container_id=container_id,
            command=command,
            deadline=deadline - reserve,
        ),
        attempt_id=attempt_id,
    )
    remote = None
    try:
        await observer.identified(helper.identity, permission="create_exec")
        await helper.send("permit_create")
        created = await helper.receive()
        if created.type != "exec_created" or not isinstance(
            created.body.get("exec_id"), str
        ):
            raise ManagedExecutionError("Docker helper did not confirm exec creation")
        exec_id = created.body["exec_id"]
        assert isinstance(exec_id, str)
        remote = DockerExecIdentity(
            attempt_id=attempt_id,
            execution_id=uuid4(),
            helper_execution_id=helper.execution_id,
            daemon_id=connection.daemon_id,
            container_id=container_id,
            exec_id=exec_id,
            start_permitted=True,
            started_observed=False,
        )
        await observer.identified(remote, permission="start_exec")
        await helper.send("permit_start")
        stdout, stderr = bytearray(), bytearray()
        truncated = {1: False, 2: False}
        while True:
            frame = await helper.receive()
            if frame.type == "exec_started":
                if remote.started_observed:
                    raise ManagedExecutionError(
                        "Docker helper reported duplicate start"
                    )
                remote = remote.model_copy(update={"started_observed": True})
                await observer.identified(remote)
            elif frame.type == "data" and remote.started_observed:
                number, content = frame.body.get("stream"), frame.body.get("content")
                if (
                    not isinstance(number, int)
                    or number not in (1, 2)
                    or not isinstance(content, str)
                ):
                    raise ManagedExecutionError("Docker helper output frame is invalid")
                output = stdout if number == 1 else stderr
                chunk = base64.b64decode(content, validate=True)
                remaining = max(0, max_output_bytes - len(output))
                output.extend(chunk[:remaining])
                truncated[number] |= len(chunk) > remaining
            elif frame.type == "finished" and remote.started_observed:
                exit_code = frame.body.get("exit_code")
                if not isinstance(exit_code, int):
                    raise ManagedExecutionError("Docker helper exit code is invalid")
                async with asyncio.timeout_at(helper.deadline):
                    await helper.process.wait()
                evidence = inspect_host_execution(helper.identity)
                if helper.process.returncode != 0 or evidence.outcome != "ended":
                    raise ManagedExecutionError(
                        "Docker helper whole group end is unconfirmed"
                    )
                await observer.ended(evidence)
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS
                    if exit_code == 0
                    else ExecutionStatus.ERROR,
                    exit_code=exit_code,
                    stdout=stdout.decode(errors="replace"),
                    stderr=stderr.decode(errors="replace"),
                    stdout_truncated=truncated[1],
                    stderr_truncated=truncated[2],
                    duration_ms=int((monotonic() - started) * 1000),
                )
            else:
                raise ManagedExecutionError("Docker helper protocol state is invalid")
    except (TimeoutError, OSError, ValueError) as exc:
        raise ManagedExecutionError(
            "Docker execution completion is unconfirmed"
        ) from exc
    finally:
        helper.deadline = deadline
        await helper.close()


async def inspect_docker_execution(
    identity: DockerExecIdentity, *, connection: DockerConnection
) -> ExecutionEvidence:
    def evidence(outcome: Literal["active", "unknown"]) -> ExecutionEvidence:
        return ExecutionEvidence(
            execution_id=identity.execution_id,
            identity=identity,
            outcome=outcome,
            started_observed=identity.started_observed,
            group_ended=False,
            issuer_ended=False,
        )

    if identity.daemon_id != connection.daemon_id:
        return evidence("unknown")
    deadline = monotonic() + 10.0
    helper = await _launch(
        _request(
            connection,
            container_id=identity.container_id,
            command=None,
            exec_id=identity.exec_id,
            deadline=deadline - 1.0,
        ),
        attempt_id=identity.attempt_id,
    )
    try:
        await helper.send("permit_inspect")
        frame = await helper.receive()
        if frame.type != "inspection" or not isinstance(
            frame.body.get("running"), bool
        ):
            return evidence("unknown")
        return evidence("active" if frame.body["running"] else "unknown")
    except (ManagedExecutionError, TimeoutError, OSError, ValueError):
        return evidence("unknown")
    finally:
        helper.deadline = deadline
        await helper.close()
