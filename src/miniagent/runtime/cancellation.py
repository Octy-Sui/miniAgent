# File   : cancellation.py
# Created: 2026-09-10 16:41:45
# Author : octy Sui

import asyncio
from collections.abc import Awaitable
from contextlib import suppress
from typing import TypeVar

T = TypeVar("T")


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


async def await_with_cancellation(
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
