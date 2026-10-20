import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

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
from miniagent.policy import PolicyEffect, PolicyEvaluation, RiskLevel
from miniagent.runtime import (
    EvaluationRequest,
    EvaluationResult,
    Evaluator,
    NewRunEvent,
    RunState,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.journal import RunJournal
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import ToolCall, ToolDefinition, ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry


class EmptyArguments(ToolArguments):
    pass


class QueueProvider(ModelProvider):
    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BlockingProvider(ModelProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class SuccessfulTool(Tool[EmptyArguments]):
    arguments_model = EmptyArguments

    def __init__(self) -> None:
        self.executions = 0

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="ok",
            description="Succeed",
            input_schema=self.arguments_model.model_json_schema(),
        )

    async def execute(
        self,
        arguments: EmptyArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        self.executions += 1
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content="done",
        )


class MismatchedResultTool(SuccessfulTool):
    async def execute(
        self,
        arguments: EmptyArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id="different-call",
            status=ToolStatus.SUCCESS,
            content="done",
        )


class RecordingEvaluator(Evaluator):
    def __init__(self, result: EvaluationResult) -> None:
        self.result = result
        self.requests: list[EvaluationRequest] = []

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        self.requests.append(request)
        return self.result


class AllowPolicyEngine:
    revision = "test-allow-v1"

    def evaluate(self, request: object) -> PolicyEvaluation:
        return PolicyEvaluation(
            effect=PolicyEffect.ALLOW,
            risk=RiskLevel.LOW,
            matched_rule_ids=("test.allow",),
            decisive_rule_ids=("test.allow",),
            reason_code="TEST_ALLOWED",
            reason="Allowed by the test policy.",
        )


class FailingCompleteStepJournal(RunJournal):
    async def complete_step(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
    ) -> None:
        raise RuntimeError("commit failed")


def final_response(content: str = "finished") -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(content=content),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )


def tool_response(
    name: str = "ok",
    arguments: dict[str, Any] | None = None,
) -> ModelResponse:
    return ModelResponse(
        message=AssistantMessage(
            tool_calls=[ToolCall(id="call", name=name, arguments=arguments or {})],
        ),
        finish_reason=FinishReason.TOOL_CALL,
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )


def agent(*, allowed_tools: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="test",
        system_prompt="Test system prompt.",
        model=ModelTarget(provider="test", model="test-model"),
        allowed_tools=allowed_tools or [],
        generation=GenerationOptions(temperature=0.25, max_output_tokens=100),
    )


def registry(*tools: Tool) -> ToolRegistry:
    return ToolRegistry(tools)


def run_state() -> RunState:
    now = datetime.now(UTC)
    return create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=agent(),
        config=RuntimeConfig(),
        created_at=now,
    )
