import asyncio
import os
import signal
from contextlib import suppress
from time import monotonic


async def reap_process(
    process: asyncio.subprocess.Process,
    tasks: list[asyncio.Task[object]],
    *,
    deadline: float,
) -> tuple[str, ...]:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    waiter = asyncio.create_task(process.wait())
    done, _ = await asyncio.wait([waiter], timeout=max(0.0, deadline - monotonic()))
    diagnostics = []
    if not done:
        waiter.cancel()
        diagnostics.append(f"CLI PID {process.pid} was not confirmed reaped")
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
