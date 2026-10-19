import asyncio
import shlex
import sys
from uuid import uuid4

import psutil
import pytest

from miniagent.environments import Command
from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    ManagedExecutionError,
    execution_scope,
)
from miniagent.environments.process import (
    identify_host_process,
    inspect_host_execution,
    reap_process,
    register_host_process,
)
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import AgentRuntime
from miniagent.runtime.events import RunEventType
from miniagent.runtime.recording import RunPersistenceError
from miniagent.storage.sqlite import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool
from tests.environments import test_local
from tests.environments.test_local import local_backend
from tests.runtime.test_runtime import AllowPolicyEngine, QueueProvider, agent

source_repository = test_local.source_repository


async def held_process():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal; print('ready', flush=True); signal.pause()",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
    return process


def identify(process):
    identity = identify_host_process(
        process.pid, attempt_id=uuid4(), execution_id=uuid4(), role="local_command"
    )
    register_host_process(process, identity)
    return identity


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


async def test_live_child_with_closed_pipes_prevents_trusted_tool_completion(
    tmp_path, source_repository
):
    """TC-U10-01: real Runtime must not commit a result while a descendant lives."""

    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import signal; signal.pause()'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    command = shlex.join([sys.executable, "-c", code])
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(
                ToolCall(id="c0", name="shell", arguments={"command": command}),
            )
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([ShellTool(workspace)]),
        policy_engine=AllowPolicyEngine(),
        run_store=store,
    )
    with pytest.raises(ManagedExecutionError, match="group end"):
        await runtime.run(
            agent(allowed_tools=["shell"]), "task", run_id=workspace.run_id
        )
    events = await store.list_events(workspace.run_id)
    assert not any(event.type is RunEventType.TOOL_END for event in events)
    operation = (await store.get(workspace.run_id)).state.execution.active_operation
    assert operation is not None and len(operation.executions) == 1
    identity = operation.executions[0]
    assert isinstance(identity, HostProcessIdentity)
    assert inspect_host_execution(identity).outcome == "ended"
