from datetime import UTC, datetime
from uuid import uuid4

import pytest

from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    HostProcessIdentity,
    ManagedExecutionError,
    current_execution_observer,
)
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime import AgentRuntime
from miniagent.runtime.events import RunEventType
from miniagent.runtime.recording import RunPersistenceError
from miniagent.storage.sqlite import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from miniagent.tools.registry import ToolRegistry
from tests.support.resume_helpers import StoreBoundary, waiting_case
from tests.support.runtime.execution_observer import ObservedTool
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    agent,
)


@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_observer_commit_failure_stops_tool_runner(tmp_path, action):
    """AC07 / F2: an uncertain identity commit must abort before the next effect."""
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    boundary = StoreBoundary(
        store, action=action, event_type=RunEventType.OPERATION_EXECUTION_IDENTIFIED
    )
    tool = ObservedTool()
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="c0", name="observed", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([tool]),
        policy_engine=AllowPolicyEngine(),
        run_store=boundary,
    )
    run_id = uuid4()
    with pytest.raises(RunPersistenceError):
        await runtime.run(agent(allowed_tools=["observed"]), "task", run_id=run_id)
    assert boundary.fired
    assert tool.effects == []
    events = await store.list_events(run_id)
    assert not any(event.type is RunEventType.TOOL_END for event in events)
    operation = (await store.get(run_id)).state.execution.active_operation
    assert operation is not None and operation.kind == "tool"
    assert current_execution_observer() is None


async def test_observer_replaces_identity_and_rejects_unconfirmed_end(tmp_path):
    store, execution, request = await waiting_case(tmp_path / "runs.sqlite3")
    await execution.resolve_approval(
        ApprovalResolution(
            approval_id=request.approval_id,
            outcome=ApprovalOutcome.APPROVED,
            actor_kind="human",
            actor_id="tester",
            reason="approved",
            resolved_at=datetime.now(UTC),
        )
    )
    await execution.begin_tool_execution()
    operation = execution.state.execution.active_operation
    assert operation is not None
    observer = execution.execution_observer(operation.attempt_id)
    identity = HostProcessIdentity(
        role="local_command",
        attempt_id=observer.attempt_id,
        execution_id=uuid4(),
        pid=123,
        create_time=1,
        host_boot_id="boot",
        pgid=123,
        sid=123,
    )
    await observer.identified(identity)
    await observer.identified(identity)
    operation = execution.state.execution.active_operation
    assert operation is not None and operation.executions == (identity,)
    evidence = ExecutionEvidence(
        execution_id=identity.execution_id,
        identity=identity,
        outcome="unknown",
        started_observed=True,
        group_ended=False,
        issuer_ended=True,
    )
    with pytest.raises(ManagedExecutionError, match="not confirmed"):
        await observer.ended(evidence)
    events = await store.list_events(request.run_id)
    assert (
        sum(
            event.type is RunEventType.OPERATION_EXECUTION_IDENTIFIED
            for event in events
        )
        == 2
    )
    assert events[-1].type is RunEventType.OPERATION_EXECUTION_ENDED
    assert not any(event.type is RunEventType.TOOL_END for event in events)
