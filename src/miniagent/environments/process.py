import asyncio
import os
import signal
import socket
from contextlib import suppress
from hashlib import sha256
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

import psutil

from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    HostProcessIdentity,
    ManagedExecutionError,
)

_OWNED_PROCESSES: WeakKeyDictionary[asyncio.subprocess.Process, HostProcessIdentity] = (
    WeakKeyDictionary()
)


def _host_boot_id() -> str:
    return sha256(
        f"{socket.gethostname()}:{psutil.boot_time():.6f}".encode()
    ).hexdigest()


def identify_host_process(
    pid: int,
    *,
    attempt_id: UUID,
    execution_id: UUID,
    role: Literal["local_command", "docker_helper", "harness"],
) -> HostProcessIdentity:
    try:
        process = psutil.Process(pid)
        created = process.create_time()
        pgid, sid = os.getpgid(pid), os.getsid(pid)
        if psutil.Process(pid).create_time() != created:
            raise ManagedExecutionError("Process identity changed during inspection")
        return HostProcessIdentity(
            role=role,
            attempt_id=attempt_id,
            execution_id=execution_id,
            pid=pid,
            create_time=created,
            host_boot_id=_host_boot_id(),
            pgid=pgid,
            sid=sid,
        )
    except (psutil.Error, OSError) as exc:
        raise ManagedExecutionError(
            "Host process birth identity is unavailable"
        ) from exc


def register_host_process(
    process: asyncio.subprocess.Process, identity: HostProcessIdentity
) -> None:
    if process.pid != identity.pid:
        raise ManagedExecutionError("Owned process handle does not match identity")
    _OWNED_PROCESSES[process] = identity


def inspect_host_execution(identity: HostProcessIdentity) -> ExecutionEvidence:
    def evidence(
        outcome: Literal["ended", "active", "unknown"],
        *,
        issuer_ended: bool = False,
        group_ended: bool = False,
    ) -> ExecutionEvidence:
        return ExecutionEvidence(
            execution_id=identity.execution_id,
            identity=identity,
            outcome=outcome,
            started_observed=True,
            group_ended=group_ended,
            issuer_ended=issuer_ended,
        )

    try:
        if _host_boot_id() != identity.host_boot_id:
            return evidence("unknown")
        try:
            leader = psutil.Process(identity.pid)
            if (
                leader.create_time() != identity.create_time
                or os.getpgid(identity.pid) != identity.pgid
                or os.getsid(identity.pid) != identity.sid
            ):
                return evidence("unknown")
            issuer_ended = leader.status() == psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, ProcessLookupError):
            issuer_ended = True
        active = False
        for pid in psutil.pids():
            try:
                if os.getpgid(pid) != identity.pgid or os.getsid(pid) != identity.sid:
                    continue
                member = psutil.Process(pid)
                birth = member.create_time()
                if birth < identity.create_time:
                    return evidence("unknown")
                if member.status() != psutil.STATUS_ZOMBIE:
                    if psutil.Process(pid).create_time() != birth:
                        return evidence("unknown")
                    active = True
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
        if active:
            return evidence("active", issuer_ended=issuer_ended)
        if not issuer_ended:
            return evidence("unknown")
        return evidence("ended", issuer_ended=True, group_ended=True)
    except (psutil.Error, OSError):
        return evidence("unknown")


def process_group_absent(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


async def reap_process(
    process: asyncio.subprocess.Process,
    tasks: list[asyncio.Task[object]],
    *,
    deadline: float,
) -> tuple[str, ...]:
    identity = _OWNED_PROCESSES.get(process)
    diagnostics = []
    if identity is None and process.returncode is None:
        try:
            identity = identify_host_process(
                process.pid,
                attempt_id=uuid4(),
                execution_id=uuid4(),
                role="local_command",
            )
        except ManagedExecutionError:
            pass
    evidence = inspect_host_execution(identity) if identity is not None else None
    if identity is not None and evidence is not None and evidence.outcome == "active":
        with suppress(ProcessLookupError):
            os.killpg(identity.pgid, signal.SIGKILL)
    elif (evidence is None and not process_group_absent(process.pid)) or (
        evidence is not None and evidence.outcome == "unknown"
    ):
        diagnostics.append(f"CLI PID {process.pid} group ownership is unconfirmed")
    waiter = asyncio.create_task(process.wait())
    done, _ = await asyncio.wait([waiter], timeout=max(0.0, deadline - monotonic()))
    if not done:
        waiter.cancel()
        diagnostics.append(f"CLI PID {process.pid} was not confirmed reaped")
    if identity is not None:
        while monotonic() < deadline:
            evidence = inspect_host_execution(identity)
            if evidence.outcome in ("ended", "unknown"):
                break
            await asyncio.sleep(min(0.01, max(0.0, deadline - monotonic())))
        if evidence is None or evidence.outcome != "ended":
            diagnostics.append(f"CLI PID {process.pid} whole group end is unconfirmed")
    for task in tasks:
        task.cancel()
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - monotonic()))
        if pending:
            diagnostics.append(f"CLI PID {process.pid} stream tasks remain pending")
    return tuple(diagnostics)


async def read_stream(
    stream: asyncio.StreamReader,
    limit: int | None,
) -> tuple[bytes, bool]:
    content = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        if limit is None:
            content.extend(chunk)
            continue
        remaining = limit - len(content)
        if remaining > 0:
            content.extend(chunk[:remaining])
        if len(chunk) > remaining:
            truncated = True
    return bytes(content), truncated


async def write_stdin(
    process: asyncio.subprocess.Process,
    content: bytes,
    *,
    unavailable_error: Exception,
) -> None:
    if process.stdin is None:
        raise unavailable_error
    try:
        process.stdin.write(content)
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        process.stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()


def decode_output(content: bytes, truncated: bool) -> str:
    text = content.decode("utf-8", errors="replace")
    if truncated:
        return f"{text}\n[output truncated]"
    return text
