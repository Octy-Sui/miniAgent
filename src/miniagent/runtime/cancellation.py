# File   : cancellation.py
# Created: 2026-09-10 16:41:45
# Author : octy Sui

import asyncio
from collections.abc import Awaitable
from contextlib import suppress

from miniagent.environments.managed_execution import cleanup_deadline_scope
from miniagent.runtime.interruption import InterruptionRequested, InterruptionToken


class CancellationRequested(Exception):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def wait(self) -> None:
        await self._event.wait()


async def await_with_cancellation[T](
    awaitable: Awaitable[T],
    token: CancellationToken,
) -> T:
    if token.is_cancelled:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        raise CancellationRequested

    operation = asyncio.ensure_future(awaitable)
    cancellation = asyncio.create_task(token.wait())

    try:
        done, _ = await asyncio.wait(
            {operation, cancellation},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation in done:
            return operation.result()

        operation.cancel()
        with suppress(asyncio.CancelledError):
            await operation
        raise CancellationRequested
    finally:
        for task in (operation, cancellation):
            if not task.done():
                task.cancel()
        for task in (operation, cancellation):
            with suppress(asyncio.CancelledError):
                await task


async def await_with_stop[T](
    awaitable: Awaitable[T],
    cancellation_token: CancellationToken,
    interruption_token: InterruptionToken | None = None,
) -> T:
    if interruption_token is None:
        return await await_with_cancellation(awaitable, cancellation_token)
    if cancellation_token.is_cancelled or interruption_token.is_requested:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        if cancellation_token.is_cancelled:
            raise CancellationRequested
        raise InterruptionRequested
    with cleanup_deadline_scope(lambda: interruption_token.deadline):
        operation = asyncio.ensure_future(awaitable)
    cancellation = asyncio.create_task(cancellation_token.wait())
    interruption = asyncio.create_task(interruption_token.wait())
    try:
        done, _ = await asyncio.wait(
            {operation, cancellation, interruption}, return_when=asyncio.FIRST_COMPLETED
        )
        if operation in done:
            return operation.result()
        operation.cancel()
        if cancellation in done:
            with suppress(asyncio.CancelledError):
                await operation
            raise CancellationRequested
        remaining = interruption_token.remaining_seconds()
        if remaining > 0:
            finished, _ = await asyncio.wait({operation}, timeout=remaining)
            if operation in finished:
                with suppress(asyncio.CancelledError):
                    operation.result()
        raise InterruptionRequested
    finally:
        for task in (operation, cancellation, interruption):
            if not task.done():
                task.cancel()
        for task in (cancellation, interruption):
            with suppress(asyncio.CancelledError):
                await task
        if not operation.done():
            operation.cancel()
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    if operation.done():
                        break
        if operation.done():
            with suppress(asyncio.CancelledError):
                operation.exception()
