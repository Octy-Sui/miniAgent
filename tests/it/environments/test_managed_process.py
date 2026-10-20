import asyncio
import sys
from uuid import uuid4

import psutil
import pytest

from miniagent.environments import Command
from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import (
    execution_scope,
)
from miniagent.environments.process import (
    inspect_host_execution,
    reap_process,
)
from miniagent.runtime.recording import RunPersistenceError
from tests.support.environments.local import local_backend
from tests.support.environments.managed_process import held_process, identify


async def test_command_completion_requires_entire_group_end():
    """TC-U10-01: a reaped leader does not prove its still-active group ended."""
    script = """
import subprocess, sys
print('ready', flush=True)
subprocess.Popen([sys.executable, '-c', "import os, signal; print('child', os.getpid(), flush=True); signal.pause()"])
sys.stdin.readline()
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
        assert (await asyncio.wait_for(process.stdout.readline(), 5)).startswith(
            b"child "
        )
        identity = identify(process)
        assert inspect_host_execution(identity).outcome == "active"
        process.stdin.write(b"exit\n")
        await process.stdin.drain()
        async with asyncio.timeout(5):
            while process.returncode is None:
                await asyncio.sleep(0.01)
        evidence = inspect_host_execution(identity)
        assert evidence.outcome == "active"
        assert evidence.issuer_ended and not evidence.group_ended
    finally:
        await reap_process(process, [], deadline=asyncio.get_running_loop().time() + 5)
    assert inspect_host_execution(identity).group_ended


async def test_reused_pid_identity_does_not_kill_neighbor():
    """TC-U10-02: a different birth time cannot establish ownership of a live PID."""
    process = await held_process()
    try:
        identity = identify(process)
        assert (
            inspect_host_execution(
                identity.model_copy(update={"create_time": identity.create_time + 1})
            ).outcome
            == "unknown"
        )
        assert process.returncode is None
    finally:
        process.kill()
        await process.wait()


async def test_identity_unreadable_is_unknown(monkeypatch):
    """TC-U10-03: external inspection denial cannot be treated as process exit."""
    process = await held_process()
    try:
        identity = identify(process)

        def denied(pid):
            raise psutil.AccessDenied(pid)

        with monkeypatch.context() as patch:
            patch.setattr(psutil, "Process", denied)
            evidence = inspect_host_execution(identity)
        assert evidence.outcome == "unknown"
        assert not evidence.group_ended
        assert process.returncode is None
    finally:
        process.kill()
        await process.wait()


async def test_identity_commit_failure_reaps_owned_group_and_propagates(
    tmp_path, source_repository
):
    """TC-U10-05: failed identity persistence must stop dispatch and reap owned work."""
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )

    class FailingObserver:
        def check_permission(self):
            pass

        attempt_id = uuid4()
        identity = None

        async def identified(self, identity, *, permission="observe"):
            self.identity = identity
            raise RunPersistenceError(
                run_id=workspace.run_id, operation="identity", last_confirmed_sequence=1
            )

        async def ended(self, evidence):
            pytest.fail("unconfirmed command must not produce trusted evidence")

    observer = FailingObserver()
    with execution_scope(observer), pytest.raises(RunPersistenceError):
        await workspace.execute(
            Command(
                argv=[sys.executable, "-c", "import signal; signal.pause()"],
                timeout_seconds=5,
            )
        )
    assert observer.identity is not None
    assert inspect_host_execution(observer.identity).outcome == "ended"
