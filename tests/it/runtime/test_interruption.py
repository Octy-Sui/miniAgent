import asyncio
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.policy import ApprovalOutcome, PolicyEffect, PolicyEvaluation, RiskLevel
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    InterruptionRequested,
    InterruptionToken,
    RunResult,
    RunStatus,
)
from miniagent.runtime.events import RunEventType
from miniagent.runtime.result import RunSuspension
from miniagent.runtime.store_contracts import RunNotFoundError
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall, ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.interruption import BlockingTool
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    BlockingProvider,
    QueueProvider,
    agent,
)


async def test_interruption_suspends_without_fabricated_tool_result(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    tool = BlockingTool()
    token = InterruptionToken()
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="call", name="block", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
    )
    running = asyncio.create_task(
        runtime.run(agent(allowed_tools=["block"]), "task", interruption_token=token)
    )
    await asyncio.wait_for(tool.started.wait(), 2)
    token.request(2)
    result = await running
    assert tool.cancelled and isinstance(result, RunSuspension)
    assert result.status is RunStatus.SUSPENDED
    assert (
        "finished_at" not in result.model_dump() and "reason" not in result.model_dump()
    )
    record = await store.get(result.run_id)
    assert record.state.lifecycle.status is RunStatus.SUSPENDED
    assert record.state.execution.active_operation is not None
    assert record.state.execution.active_operation.kind == "tool"
    events = await store.list_events(result.run_id)
    assert not any(
        event.type
        in (
            RunEventType.TOOL_END,
            RunEventType.STEP_COMPLETED,
            RunEventType.RUN_FINISHED,
        )
        for event in events
    )


async def test_explicit_cancellation_remains_terminal():
    provider = BlockingProvider()
    cancellation = CancellationToken()
    runtime = AgentRuntime(
        provider=provider, tools=ToolRegistry([]), cancellation_token=cancellation
    )
    running = asyncio.create_task(
        runtime.run(agent(), "task", interruption_token=InterruptionToken())
    )
    await provider.started.wait()
    cancellation.cancel()
    result = await running
    assert isinstance(result, RunResult) and result.status is RunStatus.CANCELLED


async def test_bare_cancelled_error_is_not_suspension(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    provider = BlockingProvider()
    runtime = AgentRuntime(provider=provider, tools=ToolRegistry([]), run_store=store)
    running = asyncio.create_task(
        runtime.run(agent(), "task", interruption_token=InterruptionToken())
    )
    await provider.started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running


@pytest.mark.parametrize(("expired",), [(False,), (True,)])
async def test_trusted_result_commits_before_pause_only_with_budget(tmp_path, expired):
    now = [monotonic()]
    token = InterruptionToken(timeout_seconds=2, clock=lambda: now[0])
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()

    class ReturningTool(BlockingTool):
        async def execute(self, arguments, *, tool_call_id):
            token.request(15)
            if expired:
                now[0] += 3
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.SUCCESS,
                content="trusted actual result",
            )

    tool = ReturningTool()
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="call", name="block", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
    )
    if expired:
        with pytest.raises(InterruptionRequested):
            await runtime.run(
                agent(allowed_tools=["block"]), "task", interruption_token=token
            )
    else:
        result = await runtime.run(
            agent(allowed_tools=["block"]), "task", interruption_token=token
        )
        assert isinstance(result, RunSuspension)
        events = await store.list_events(result.run_id)
        assert sum(event.type == RunEventType.TOOL_END for event in events) == 1
        assert not any(event.type == RunEventType.STEP_COMPLETED for event in events)


async def test_pending_approval_is_invalidated_atomically_with_suspension(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    token = InterruptionToken()

    class ApprovalPolicy:
        revision = "approval"

        def evaluate(self, request):
            return PolicyEvaluation(
                effect=PolicyEffect.APPROVAL,
                risk=RiskLevel.HIGH,
                matched_rule_ids=("approval",),
                decisive_rule_ids=("approval",),
                reason_code="APPROVAL",
                reason="approval",
            )

    class Handler:
        def __init__(self):
            self.started = asyncio.Event()
            self.request = None

        async def resolve(self, request, decision):
            self.request = request
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    handler = Handler()
    tool = BlockingTool()
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="call", name="block", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=ApprovalPolicy(),
        approval_handler=handler,
    )
    running = asyncio.create_task(
        runtime.run(agent(allowed_tools=["block"]), "task", interruption_token=token)
    )
    await asyncio.wait_for(handler.started.wait(), 2)
    token.request(2)
    result = await running
    assert isinstance(result, RunSuspension)
    assert handler.request is not None
    stored = await store.get_approval(handler.request.approval_id)
    assert stored.approval.resolution is not None
    assert stored.approval.resolution.outcome is ApprovalOutcome.INVALIDATED
    assert not tool.started.is_set()


async def test_interruption_before_initial_record_creates_no_run(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    token = InterruptionToken()
    token.request(2)
    run_id = uuid4()
    with pytest.raises(InterruptionRequested):
        await AgentRuntime(
            provider=QueueProvider([]), tools=ToolRegistry([]), run_store=store
        ).run(agent(), "task", run_id=run_id, interruption_token=token)
    with pytest.raises(RunNotFoundError):
        await store.get(run_id)
