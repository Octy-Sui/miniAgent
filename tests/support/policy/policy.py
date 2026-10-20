import asyncio
from datetime import UTC, datetime

from miniagent.agents import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import ToolCall, ToolDefinition, ToolResult, ToolStatus


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
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def final_response() -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content="done"),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def make_agent() -> AgentSpec:
    return AgentSpec(
        name="test",
        system_prompt="Test.",
        generation=GenerationOptions(max_output_tokens=100),
        model=ModelTarget(provider="test", model="test"),
        allowed_tools=["shell"],
    )
