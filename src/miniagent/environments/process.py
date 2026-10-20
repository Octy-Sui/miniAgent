import asyncio
import os
import signal
import socket
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import BinaryIO, Literal
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

import psutil

from miniagent.environments.evidence import CommandEvidence
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    ExecutionObserver,
    HostProcessIdentity,
    ManagedExecutionError,
    current_cleanup_deadline,
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
    shared_deadline = current_cleanup_deadline()
    if shared_deadline is not None:
        deadline = min(deadline, shared_deadline)
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


async def start_host_process(
    argv: list[str],
    *,
    role: Literal["local_command", "docker_helper", "harness"],
    attempt_id: UUID,
    observer: ExecutionObserver | None,
    command: CommandEvidence | None = None,
    deadline: float,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    stdin: int | None = None,
    stdout: int | BinaryIO = asyncio.subprocess.PIPE,
    stderr: int | BinaryIO = asyncio.subprocess.PIPE,
) -> tuple[asyncio.subprocess.Process, HostProcessIdentity]:
    deadline = min(deadline, current_cleanup_deadline() or float("inf"))
    if deadline <= monotonic():
        raise TimeoutError("Host execution deadline exhausted")
    if observer is not None:
        observer.check_permission()
    read_fd, write_fd = os.pipe()
    error_read, error_write = os.pipe()
    process = None
    identified = False
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(Path(__file__).with_name("host_exec.py")),
            str(read_fd),
            str(deadline),
            str(error_write),
            *argv,
            cwd=cwd,
            env=env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            pass_fds=(read_fd, error_write),
            start_new_session=True,
        )
        os.close(read_fd)
        read_fd = None
        os.close(error_write)
        error_write = None
        identity = identify_host_process(
            process.pid, attempt_id=attempt_id, execution_id=uuid4(), role=role
        )
        register_host_process(process, identity)
        if observer is not None:
            await observer.identified(identity, command=command)
            identified = True
            observer.check_permission()
        if monotonic() >= deadline:
            raise TimeoutError("Host execution permission deadline exhausted")
        os.write(write_fd, b"1")
        loop = asyncio.get_running_loop()
        readable: asyncio.Future[None] = loop.create_future()

        def ready() -> None:
            if not readable.done():
                readable.set_result(None)

        loop.add_reader(error_read, ready)
        try:
            async with asyncio.timeout_at(deadline):
                await readable
                error = os.read(error_read, 4)
        finally:
            loop.remove_reader(error_read)
        if error:
            number = int.from_bytes(error, "big")
            raise OSError(number, os.strerror(number), argv[0])
        return process, identity
    except BaseException as exc:
        # EOF releases an unpermitted launcher even when its identity is unavailable.
        os.close(write_fd)
        write_fd = None
        if process is not None:
            diagnostics = await reap_process(
                process, [], deadline=min(deadline, monotonic() + 5.0)
            )
            for diagnostic in diagnostics:
                exc.add_note(diagnostic)
            if isinstance(exc, (OSError, TimeoutError, asyncio.CancelledError)):
                if diagnostics:
                    raise ManagedExecutionError(
                        "Host launcher cleanup is unconfirmed"
                    ) from exc
                if observer is not None and identified:
                    await observer.ended(inspect_host_execution(identity))
        raise
    finally:
        for descriptor in (read_fd, write_fd, error_read, error_write):
            if descriptor is not None:
                os.close(descriptor)


async def read_stream(
    stream: asyncio.StreamReader,
    limit: int | None,
    *,
    on_chunk: Callable[[bytes], Awaitable[None]] | None = None,
) -> tuple[bytes, bool]:
    content = bytearray()
    truncated = False
    while chunk := await stream.read(64 * 1024):
        if on_chunk is not None:
            await on_chunk(chunk)
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
