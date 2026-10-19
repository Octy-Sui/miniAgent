import asyncio
import json
import threading
from pathlib import Path

import pytest

from miniagent.dashboard.reads import ReadFailure
from miniagent.dashboard.workers import HeavyWorkPool


def operation(path: Path, started: threading.Event, release: threading.Event) -> int:
    started.set()
    if not release.wait(5):
        raise TimeoutError("test worker handshake timed out")
    return json.loads(path.read_text())["value"]


async def start_two(pool: HeavyWorkPool, tmp_path: Path):
    path = tmp_path / "data.json"
    path.write_text('{"value": 42}')
    starts = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    tasks = [
        asyncio.create_task(
            pool.run(lambda i=i: operation(path, starts[i], releases[i]))
        )
        for i in range(2)
    ]
    for event in starts:
        assert await asyncio.to_thread(event.wait, 2)
    return tasks, releases


async def test_third_heavy_work_is_rejected(tmp_path: Path) -> None:
    pool = HeavyWorkPool(limit=2)
    tasks, releases = await start_two(pool, tmp_path)
    try:
        with pytest.raises(ReadFailure, match="heavy_work_busy"):
            await pool.run(lambda: 3)
    finally:
        for event in releases:
            event.set()
        assert await asyncio.gather(*tasks) == [42, 42]
        await pool.aclose()


async def test_http_cancellation_keeps_actual_work_slot(tmp_path: Path) -> None:
    pool = HeavyWorkPool(limit=2)
    tasks, releases = await start_two(pool, tmp_path)
    try:
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        with pytest.raises(ReadFailure, match="heavy_work_busy"):
            await pool.run(lambda: 3)
        releases[1].set()
        assert await tasks[1] == 42
        assert await pool.run(lambda: 3) == 3
    finally:
        for event in releases:
            event.set()
        await pool.aclose()


async def test_close_waits_for_actual_workers(tmp_path: Path) -> None:
    pool = HeavyWorkPool(limit=2)
    tasks, releases = await start_two(pool, tmp_path)
    closing = asyncio.create_task(pool.aclose())
    await asyncio.sleep(0)
    try:
        assert not closing.done()
        with pytest.raises(ReadFailure, match="heavy_work_closed"):
            await pool.run(lambda: 3)
    finally:
        for event in releases:
            event.set()
        await closing
        assert await asyncio.gather(*tasks) == [42, 42]


async def test_exception_propagates_and_releases_slot() -> None:
    pool = HeavyWorkPool(limit=1)

    def fail() -> int:
        raise ValueError("operation failed")

    with pytest.raises(ValueError, match="operation failed"):
        await pool.run(fail)
    assert await pool.run(lambda: 7) == 7
    await pool.aclose()
