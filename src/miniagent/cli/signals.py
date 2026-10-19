import asyncio
import signal
from collections.abc import Awaitable, Callable
from contextlib import suppress

from miniagent.environments.managed_execution import cleanup_deadline_scope
from miniagent.runtime import CancellationToken, InterruptionToken
from miniagent.runtime.interruption import interruption_scope


class _ForcedSignalCancellation(Exception):
    def __init__(self, received_signal: signal.Signals) -> None:
        self.received_signal = received_signal


async def run_with_signals[T](
    operation: Callable[[CancellationToken, InterruptionToken], Awaitable[T]],
) -> tuple[T, signal.Signals | None]:
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    cancellation = CancellationToken()
    interruption = InterruptionToken()
    received: signal.Signals | None = None

    def handle(current: signal.Signals) -> None:
        nonlocal received
        if received is not None:
            if main_task is not None:
                main_task.cancel()
            return
        received = current
        interruption.request(2 if current is signal.SIGINT else 15)

    registered = []
    for current in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(current, handle, current)
        except NotImplementedError:
            continue
        registered.append(current)

    async def expire() -> None:
        await interruption.wait()
        await asyncio.sleep(interruption.remaining_seconds())
        if main_task is not None:
            main_task.cancel()

    watchdog = asyncio.create_task(expire())
    try:
        with (
            interruption_scope(interruption),
            cleanup_deadline_scope(lambda: interruption.deadline),
        ):
            result = await operation(cancellation, interruption)
        return result, received
    except asyncio.CancelledError as exc:
        if received is not None:
            raise _ForcedSignalCancellation(received) from exc
        raise
    except Exception as exc:
        if received is not None:
            raise _ForcedSignalCancellation(received) from exc
        raise
    finally:
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog
        for current in registered:
            loop.remove_signal_handler(current)
