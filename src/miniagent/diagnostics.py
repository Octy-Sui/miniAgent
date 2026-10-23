"""Best-effort diagnostics; journal events remain the recovery authority."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

_FIELDS = frozenset(
    [
        "invocation_id",
        "approval_id",
        "outcome",
        "stdout_bytes",
        "stderr_bytes",
        "run_id",
        "step_number",
        "span_id",
        "attempt_id",
        "phase",
        "duration_ms",
        "elapsed_seconds",
        "remaining_seconds",
        "error_code",
        "error_type",
        "model",
        "provider",
        "tool_name",
        "tool_call_id",
        "attempt",
        "finish_reason",
        "tool_call_count",
        "status",
        "failed",
        "passed",
        "reason",
        "cost_usd",
        "cost_status",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "sequence",
        "old_input_tokens",
        "new_input_tokens",
        "workspace_id",
        "workspace_kind",
        "exit_code",
        "artifact_id",
        "log_path",
        "signal",
        "retry_delay_seconds",
        "worker_index",
        "completed",
        "total",
        "running",
        "instance_id",
        "output_directory",
        "dropped_count",
    ]
)
_context: ContextVar[dict[str, object] | None] = ContextVar("log_context", default=None)
heartbeat_interval: ContextVar[float] = ContextVar("heartbeat_interval", default=0)
_logger = logging.getLogger("miniagent.progress")


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    safe = {
        key: str(value)[:512]
        if not isinstance(value, (str, int, float, bool))
        else value
        for key, value in ((_context.get() or {}) | fields).items()
        if key in _FIELDS and value is not None
    }
    safe = {
        key: value[:512] if isinstance(value, str) else value
        for key, value in safe.items()
    }
    try:
        _logger.log(level, event, extra={"fields": safe})
    except Exception:  # noqa: BLE001, S110 -- diagnostics cannot change business outcomes.
        pass


@dataclass(slots=True)
class _Operation:
    children: int = 0


_active: ContextVar[_Operation | None] = ContextVar("log_operation", default=None)


@asynccontextmanager
async def operation(
    phase: str,
    *,
    interval_seconds: float | None = None,
    lifecycle: bool = True,
    **fields: object,
) -> AsyncIterator[None]:
    """Observe an operation without owning its timeout or cancellation policy."""
    interval = (
        heartbeat_interval.get() if interval_seconds is None else interval_seconds
    )
    started = monotonic()
    parent = _active.get()
    current = _Operation()
    if parent is not None:
        parent.children += 1
    token = _active.set(current)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(interval)
            if current.children == 0:
                log_event(
                    "operation.waiting", elapsed_seconds=round(monotonic() - started, 1)
                )

    with log_context(phase=phase, **fields):
        task = (
            asyncio.create_task(heartbeat(), name=f"heartbeat:{phase}")
            if interval > 0
            else None
        )
        if lifecycle:
            log_event(f"{phase}.started")
        try:
            yield
        except BaseException as exc:
            if lifecycle:
                log_event(
                    f"{phase}.interrupted"
                    if isinstance(exc, asyncio.CancelledError)
                    else f"{phase}.failed",
                    level=logging.INFO
                    if isinstance(exc, asyncio.CancelledError)
                    else logging.WARNING,
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", None),
                    duration_ms=round((monotonic() - started) * 1000, 1),
                )
            raise
        else:
            if lifecycle:
                log_event(
                    f"{phase}.completed",
                    duration_ms=round((monotonic() - started) * 1000, 1),
                )
        finally:
            try:
                if task is not None:
                    task.cancel()
                    # Consume child cancellation without swallowing caller cancellation.
                    await asyncio.gather(task, return_exceptions=True)
            finally:
                _active.reset(token)
                if parent is not None:
                    parent.children -= 1
