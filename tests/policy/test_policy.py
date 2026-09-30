import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest

from miniagent.agents import AgentSpec
from miniagent.models.config import ModelTarget, GenerationOptions
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    DefaultPolicyEngine,
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
    UnknownOperation,
    build_policy_request,
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
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import ToolCall, ToolDefinition, ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry


class CommandArguments(ToolArguments):
    command: str


class RecordingShell(Tool[CommandArguments]):
    arguments_model = CommandArguments

    def __init__(self) -> None:
        self.executions = 0

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell",
            description="Run a command.",
            input_schema=self.arguments_model.model_json_schema(),
        )

    async def execute(
        self,
        arguments: CommandArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        self.executions += 1
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content="executed",
        )


class QueueProvider(ModelProvider):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class ResolvingApprovalHandler:
    def __init__(self, outcome: ApprovalOutcome) -> None:
        self.outcome = outcome
        self.requests: list[ApprovalRequest] = []

    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution:
        self.requests.append(request)
        return ApprovalResolution(
            approval_id=request.approval_id,
            outcome=self.outcome,
            actor_kind="human",
            actor_id="tester",
            reason="Test resolution.",
            resolved_at=datetime.now(UTC),
        )


class BlockingApprovalHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.parametrize(
    ("path", "workspace_kind", "expected_effect", "expected_rule"),
    [
        ("../secret", "local", PolicyEffect.DENY, "path.outside_workspace"),
        ("src/main.py", "local", PolicyEffect.ALLOW, "read.workspace"),
        ("src/main.py", "docker", PolicyEffect.ALLOW, "read.workspace"),
    ],
)
def test_default_read_policy(
    path: str,
    workspace_kind: Literal["local", "docker"],
    expected_effect: PolicyEffect,
    expected_rule: str,
) -> None:
    agent = make_agent()
    definition = ToolDefinition(
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object"},
    )
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="read",
        tool_name="read_file",
        arguments={"path": path},
        agent=agent,
        definition=definition,
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind=workspace_kind,
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert evaluation.effect is expected_effect
    assert evaluation.decisive_rule_ids == (expected_rule,)


@pytest.mark.parametrize(
    ("workspace_kind", "expected_effect", "expected_rule"),
    [
        ("local", PolicyEffect.APPROVAL, "shell.local_human_review"),
        ("docker", PolicyEffect.ALLOW, "shell.docker_workspace"),
    ],
)
def test_default_shell_policy(
    workspace_kind: Literal["local", "docker"],
    expected_effect: PolicyEffect,
    expected_rule: str,
) -> None:
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="shell",
        tool_name="shell",
        arguments={"command": "pwd"},
        agent=make_agent(),
        definition=RecordingShell().definition,
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind=workspace_kind,
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert evaluation.effect is expected_effect
    assert evaluation.decisive_rule_ids == (expected_rule,)


def test_unknown_tool_maps_to_typed_operation_and_is_denied() -> None:
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="unknown",
        tool_name="unknown_tool",
        arguments={"risk": "low"},
        agent=make_agent(),
        definition=ToolDefinition(
            name="unknown_tool",
            description="Unknown.",
            input_schema={"type": "object"},
        ),
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind="docker",
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert isinstance(request.operation, UnknownOperation)
    assert evaluation.effect is PolicyEffect.DENY
    assert evaluation.reason_code == "NO_ALLOWING_POLICY"
    assert DefaultPolicyEngine().revision == "default-v4"


async def test_approved_shell_call_executes_once() -> None:
    tool = RecordingShell()
    provider = QueueProvider([shell_response(), final_response()])
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        approval_handler=approvals,
    )

    result = await runtime.run(make_agent(), "task")

    assert result.status is RunStatus.SUCCEEDED
    assert tool.executions == 1
    assert result.usage.tool_calls == 1
    assert len(approvals.requests) == 1


async def test_approved_shell_call_persists_approval_atomically(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
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
) -> None:
    store = SQLiteRunStore(
        tmp_path / "runs.sqlite3",
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    await store.initialize()
    tool = RecordingShell()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.APPROVED)
    runtime = AgentRuntime(
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


async def test_docker_shell_call_executes_without_approval() -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
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


async def test_rejected_shell_call_stops_without_execution() -> None:
    tool = RecordingShell()
    approvals = ResolvingApprovalHandler(ApprovalOutcome.REJECTED)
    runtime = AgentRuntime(
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
        approval_handler=approvals,
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_REJECTED
    assert tool.executions == 0
    assert result.usage.tool_calls == 0


async def test_missing_approval_handler_fails_closed() -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_UNAVAILABLE
    assert tool.executions == 0


async def test_approval_request_expires_without_execution() -> None:
    tool = RecordingShell()
    runtime = AgentRuntime(
        provider=QueueProvider([shell_response()]),
        tools=ToolRegistry([tool]),
        approval_handler=BlockingApprovalHandler(),
        config=RuntimeConfig(approval_timeout_seconds=0.01),
    )

    result = await runtime.run(make_agent(), "task")

    assert result.reason is TerminationReason.APPROVAL_EXPIRED
    assert tool.executions == 0


async def test_cancelling_in_memory_approval_returns_cancelled_result() -> None:
    token = CancellationToken()
    approvals = BlockingApprovalHandler()
    runtime = AgentRuntime(
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


def shell_response() -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(
            tool_calls=[
                ToolCall(
                    id="shell-call",
                    name="shell",
                    arguments={"command": "pwd"},
                )
            ],
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def final_response() -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content="done"),
        finish_reason=FinishReason.STOP,
    )


def make_agent() -> AgentSpec:
    return AgentSpec(
        name="test",
        system_prompt="Test.",
        generation=GenerationOptions(max_output_tokens=100),
        model=ModelTarget(provider="test", model="test"),
        allowed_tools=["shell"],
    )
