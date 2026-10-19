import asyncio
from collections.abc import Callable
from typing import Any

from miniagent.dashboard.reads import ReadFailure


class HeavyWorkPool:
    def __init__(self, limit: int = 2) -> None:
        if limit < 1:
            raise ValueError("heavy work limit must be positive")
        self._limit = limit
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    async def run[T](self, operation: Callable[[], T]) -> T:
        if self._closed:
            raise ReadFailure(503, "heavy_work_closed")
        if len(self._tasks) >= self._limit:
            raise ReadFailure(503, "heavy_work_busy")
        task = asyncio.create_task(asyncio.to_thread(operation))
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def aclose(self) -> None:
        self._closed = True
        if self._tasks:
            await asyncio.shield(asyncio.gather(*self._tasks, return_exceptions=True))
