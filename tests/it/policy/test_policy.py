import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from miniagent.policy import (
    ApprovalOutcome,
    PolicyContext,
)
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    RunEventType,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
)
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.policy.policy import (
    BlockingApprovalHandler,
    QueueProvider,
    RecordingShell,
    ResolvingApprovalHandler,
    final_response,
    make_agent,
    shell_response,
)


async def test_approved_shell_call_executes_once(runtime_persistence) -> None:
    tool = RecordingShell()
    provider = QueueProvider([shell_response(), final_response()])
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=provider,
        tools=ToolRegistry([tool]),
        approval_handler=approvals,
    )

    result = await runtime.run(make_agent(), "task")

    assert result.status is RunStatus.SUCCEEDED
    assert tool.executions == 1
    assert result.usage.tool_calls == 1
    assert len(approvals.requests) == 1


async def test_docker_shell_call_executes_without_approval(runtime_persistence) -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=QueueProvider([shell_response(), final_response()]),
        tools=ToolRegistry([tool]),
        policy_context=PolicyContext(
            workspace_id="workspace",
            workspace_kind="docker",
            revision="revision",
        ),
    )

    result = await runtime.run(make_agent(), "task")

    assert result.status is RunStatus.SUCCEEDED
    assert tool.executions == 1
    assert result.usage.tool_calls == 1


async def test_rejected_shell_call_stops_without_execution(runtime_persistence) -> None:
    tool = RecordingShell()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.REJECTED)
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
        approval_handler=approvals,
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_REJECTED
    assert tool.executions == 0
    assert result.usage.tool_calls == 0


async def test_missing_approval_handler_fails_closed(runtime_persistence) -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_UNAVAILABLE
    assert tool.executions == 0


async def test_approval_request_expires_without_execution(runtime_persistence) -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
        approval_handler=BlockingApprovalHandler(),
        config=RuntimeConfig(approval_timeout_seconds=0.01),
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_EXPIRED
    assert tool.executions == 0


async def test_cancelling_in_memory_approval_returns_cancelled_result(
    runtime_persistence,
) -> None:
    token = CancellationToken()
    approvals = BlockingApprovalHandler()
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([RecordingShell()]),
        approval_handler=approvals,
        cancellation_token=token,
    )
    run = asyncio.create_task(runtime.run(make_agent(), "task"))
    await approvals.started.wait()

    token.cancel()
    result = await asyncio.wait_for(run, timeout=1)

    assert result.status is RunStatus.CANCELLED
    assert result.reason is TerminationReason.CANCEL_REQUESTED


async def test_approved_shell_call_persists_approval_atomically(
    tmp_path: Path,
    runtime_persistence,
) -> None:
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([shell_response(), final_response()]),
        tools=ToolRegistry([RecordingShell()]),
        approval_handler=approvals,
        run_store=store,
    )

    result = await runtime.run(make_agent(), "task")

    approval = await store.get_approval(approvals.requests[0].approval_id)
    events = await store.list_events(result.run_id)
    assert approval.approval.resolution is not None
    assert approval.approval.resolution.outcome is ApprovalOutcome.APPROVED
    assert await store.list_pending_approvals() == []
    assert {event.type for event in events} >= {
        RunEventType.APPROVAL_REQUESTED,
        RunEventType.APPROVAL_RESOLVED,
    }
    event_types = [event.type for event in events]
    assert event_types.index(RunEventType.TOOL_START) < event_types.index(
        RunEventType.APPROVAL_REQUESTED
    )
    assert event_types.index(RunEventType.APPROVAL_RESOLVED) < event_types.index(
        RunEventType.TOOL_EXECUTION_START
    )
    assert event_types.index(RunEventType.TOOL_EXECUTION_END) < event_types.index(
        RunEventType.TOOL_END
    )
    tool_start = next(
        event for event in events if event.type is RunEventType.TOOL_START
    )
    approval_requested = next(
        event for event in events if event.type is RunEventType.APPROVAL_REQUESTED
    )
    approval_resolved = next(
        event for event in events if event.type is RunEventType.APPROVAL_RESOLVED
    )
    execution_start = next(
        event for event in events if event.type is RunEventType.TOOL_EXECUTION_START
    )
    assert approval_requested.span_id == approval_resolved.span_id
    assert approval_requested.span_id != tool_start.span_id
    assert approval_requested.parent_span_id == tool_start.span_id
    assert approval_resolved.parent_span_id == tool_start.span_id
    assert approval_requested.step_number == tool_start.step_number
    assert approval_resolved.step_number == tool_start.step_number
    assert execution_start.span_id != approval_requested.span_id
    assert execution_start.parent_span_id == tool_start.span_id
    assert execution_start.step_number == tool_start.step_number


async def test_approval_expiring_during_commit_is_persisted_as_expired(
    tmp_path: Path,
    runtime_persistence,
) -> None:
    store = SQLiteRunStore(
        tmp_path / "runs.sqlite3",
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    await store.initialize()
    tool = RecordingShell()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
        approval_handler=approvals,
        run_store=store,
    )

    result = await runtime.run(make_agent(), "task")

    record = await store.get(result.run_id)
    approval = await store.get_approval(approvals.requests[0].approval_id)
    events = await store.list_events(result.run_id)
    assert result.reason is TerminationReason.APPROVAL_EXPIRED
    assert record.state.lifecycle.status is RunStatus.FAILED
    assert approval.approval.resolution is not None
    assert approval.approval.resolution.outcome is ApprovalOutcome.EXPIRED
    assert tool.executions == 0
    assert RunEventType.TOOL_EXECUTION_START not in {event.type for event in events}
    event_types = [event.type for event in events]
    assert event_types.index(RunEventType.APPROVAL_RESOLVED) < event_types.index(
        RunEventType.TOOL_END
    )
