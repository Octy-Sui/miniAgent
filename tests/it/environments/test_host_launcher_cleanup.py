import asyncio
import sys
from time import monotonic
from uuid import uuid4

import psutil
import pytest

from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import ManagedExecutionError
from miniagent.environments.process import start_host_process
from tests.support.environments import local as test_local
from tests.support.environments.local import local_backend

source_repository = test_local.source_repository


@pytest.fixture
async def unreadable_launchers(monkeypatch):
    processes = []
    spawn = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        processes.append(process)
        return process

    def denied():
        raise PermissionError("host boot time unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(psutil, "boot_time", denied)
    yield processes
    for process in processes:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize(("deadline_seconds",), [(None,), (30.0,)])
async def test_identity_failure_releases_unpermitted_launcher(
    tmp_path, unreadable_launchers, deadline_seconds
):
    marker = tmp_path / "executed"
    task = asyncio.create_task(
        start_host_process(
            ["touch", str(marker)],
            role="local_command",
            attempt_id=uuid4(),
            observer=None,
            deadline=(
                float("inf")
                if deadline_seconds is None
                else monotonic() + deadline_seconds
            ),
        )
    )
    try:
        done, _ = await asyncio.wait([task], timeout=2)
        assert task in done, "identity failure blocked waiting for launcher permission"
        with pytest.raises(ManagedExecutionError, match="birth identity"):
            await task
        assert len(unreadable_launchers) == 1
        assert unreadable_launchers[0].returncode == 125
        assert not marker.exists()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_workspace_creation_identity_failure_does_not_hang(
    tmp_path, source_repository, unreadable_launchers
):
    backend = local_backend(tmp_path)
    task = asyncio.create_task(
        backend.create(
            LocalWorkspaceSpec(
                run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
            )
        )
    )
    try:
        done, _ = await asyncio.wait([task], timeout=2)
        assert task in done, "workspace creation blocked after identity lookup failed"
        with pytest.raises(ManagedExecutionError, match="birth identity"):
            await task
        assert unreadable_launchers
        assert all(process.returncode == 125 for process in unreadable_launchers)
        assert not list(backend.root.rglob("repo"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(("deadline_seconds",), [(None,), (0.2,)])
async def test_identity_failure_cleanup_is_bounded_when_launcher_stalls(
    unreadable_launchers, monkeypatch, deadline_seconds
):
    spawn = asyncio.create_subprocess_exec

    async def stalled(*args, **kwargs):
        return await spawn(
            sys.executable, "-c", "import time; time.sleep(60)", **kwargs
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", stalled)
    task = asyncio.create_task(
        start_host_process(
            ["unused-target"],
            role="local_command",
            attempt_id=uuid4(),
            observer=None,
            deadline=(
                float("inf")
                if deadline_seconds is None
                else monotonic() + deadline_seconds
            ),
        )
    )
    try:
        done, _ = await asyncio.wait(
            [task], timeout=8 if deadline_seconds is None else 2
        )
        assert task in done, "failed launcher cleanup has no finite budget"
        with pytest.raises(ManagedExecutionError, match="birth identity") as caught:
            await task
        assert any("not confirmed reaped" in note for note in caught.value.__notes__)
        assert unreadable_launchers[0].returncode is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
