import shlex
import sys
from uuid import uuid4

import pytest

from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    ManagedExecutionError,
)
from miniagent.environments.process import (
    inspect_host_execution,
)
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import AgentRuntime
from miniagent.runtime.events import RunEventType
from miniagent.storage.sqlite import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool
from tests.support.environments.local import local_backend
from tests.support.runtime.runtime import AllowPolicyEngine, QueueProvider, agent


async def test_live_child_with_closed_pipes_prevents_trusted_tool_completion(
    tmp_path, source_repository, runtime_persistence
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
        artifact_store=runtime_persistence.artifacts,
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
