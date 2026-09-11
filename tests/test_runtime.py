import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.models import PriceConfig
from miniagent.models.model import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    Usage,
)
from miniagent.models.pricing import ModelPrice
from miniagent.models.tool import ToolCall, ToolDefinition, ToolResult, ToolStatus
from miniagent.providers import ModelProvider
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    EvaluationRequest,
    EvaluationResult,
    Evaluator,
    InvalidRunTransition,
    RunCursor,
    RunError,
    RunResult,
    RunState,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
    transition,
)
from miniagent.tools import Tool


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


class SuccessfulTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="ok", description="Succeed", input_schema={})

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
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


def final_response(content: str = "finished") -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=content),
        finish_reason=FinishReason.STOP,
    )


def tool_response(name: str = "ok") -> ModelResponse:
    return ModelResponse(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="call", name=name, arguments={})],
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def agent(*, allowed_tools: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="test",
        system_prompt="Test system prompt.",
        model=ModelTarget(provider="test", model="test-model"),
        allowed_tools=allowed_tools or [],
        temperature=0.25,
        max_output_tokens=100,
    )


def run_state() -> RunState:
    now = datetime.now(UTC)
    return RunState(
        run_id=uuid4(),
        task="task",
        agent_spec=agent(),
        config=RuntimeConfig(),
        created_at=now,
        updated_at=now,
    )


class TestRunState:
    def test_valid_lifecycle_reaches_succeeded(self) -> None:
        state = run_state()

        transition(state, RunStatus.PREPARING)
        transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)
        state.candidate_output = "candidate"
        transition(state, RunStatus.EVALUATING)
        state.final_output = "verified"
        transition(
            state,
            RunStatus.SUCCEEDED,
            reason=TerminationReason.TASK_VERIFIED,
        )

        assert state.status == RunStatus.SUCCEEDED
        assert state.version == 4
        assert state.started_at is not None
        assert state.finished_at is not None

        result = RunResult.from_state(state)
        assert result.run_id == state.run_id
        assert result.output == "verified"
        assert result.usage == state.usage

    def test_illegal_transition_does_not_mutate_state(self) -> None:
        state = run_state()

        with pytest.raises(InvalidRunTransition):
            transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)

        assert state.status == RunStatus.CREATED
        assert state.version == 0

    def test_cancelled_before_start_can_produce_result(self) -> None:
        state = run_state()
        transition(
            state,
            RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
        )

        result = RunResult.from_state(state)

        assert result.status == RunStatus.CANCELLED
        assert result.started_at is None

    def test_invalid_target_invariants_do_not_mutate_state(self) -> None:
        state = run_state()
        transition(state, RunStatus.PREPARING)
        transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)

        with pytest.raises(ValueError, match="candidate output"):
            transition(state, RunStatus.EVALUATING)

        assert state.status == RunStatus.RUNNING
        assert state.cursor == RunCursor.REQUEST_MODEL
        assert state.version == 2

    def test_reserved_recovery_fields_have_empty_defaults(self) -> None:
        state = run_state()

        assert state.usage.model_requests == 0
        assert state.completed_tool_call_ids == set()
        assert state.pending_approval_id is None
        assert state.checkpoint_id is None


class TestAgentRuntime:
    async def test_model_usage_and_cost_accumulate_across_steps(self) -> None:
        first = tool_response()
        first.usage = Usage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            reasoning_output_tokens=5,
            total_tokens=120,
        )
        second = final_response()
        second.usage = Usage(
            input_tokens=200,
            cached_input_tokens=50,
            output_tokens=30,
            reasoning_output_tokens=10,
            total_tokens=230,
        )
        pricing = PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=Decimal("0.0000004"),
                    cached_input_per_token=Decimal("0.0000001"),
                    output_per_token=Decimal("0.0000012"),
                )
            ]
        )
        runtime = AgentRuntime(
            provider=QueueProvider([first, second]),
            tools={"ok": SuccessfulTool()},
            pricing=pricing,
        )

        result = await runtime.run(agent(allowed_tools=["ok"]), "task")

        assert result.status == RunStatus.SUCCEEDED
        assert result.usage.model_requests == 2
        assert result.usage.total_tokens == 350
        assert result.usage.reasoning_output_tokens == 15
        assert result.usage.cached_input_priced_separately
        assert result.usage.input_cost_usd == Decimal("0.0000840")
        assert result.usage.cached_input_cost_usd == Decimal("0.0000090")
        assert result.usage.cost_usd == Decimal("0.0001530")

    async def test_model_stop_is_evaluated_before_run_succeeds(self) -> None:
        evaluator = RecordingEvaluator(
            EvaluationResult(passed=True, output="verified output")
        )
        runtime = AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools={},
            evaluator=evaluator,
        )

        result = await runtime.run(agent(), "task")

        assert result.status == RunStatus.SUCCEEDED
        assert result.reason == TerminationReason.TASK_VERIFIED
        assert result.output == "verified output"
        assert len(evaluator.requests) == 1
        assert evaluator.requests[0].candidate_output == "finished"
        assert evaluator.requests[0].run_id == result.run_id

    async def test_failed_evaluation_fails_run(self) -> None:
        evaluator = RecordingEvaluator(
            EvaluationResult(
                passed=False,
                error=RunError(code="TESTS_FAILED", message="Tests failed"),
            )
        )
        runtime = AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools={},
            evaluator=evaluator,
        )

        result = await runtime.run(agent(), "task")

        assert result.status == RunStatus.FAILED
        assert result.reason == TerminationReason.EVALUATION_FAILED
        assert result.error is not None
        assert result.error.code == "TESTS_FAILED"

    async def test_step_limit_stops_tool_loop(self) -> None:
        provider = QueueProvider([tool_response(), tool_response()])
        runtime = AgentRuntime(
            provider=provider,
            tools={"ok": SuccessfulTool()},
            config=RuntimeConfig(max_steps=2),
        )

        result = await runtime.run(agent(allowed_tools=["ok"]), "task")

        assert result.status == RunStatus.FAILED
        assert result.reason == TerminationReason.MAX_STEPS_REACHED
        assert len(provider.requests) == 2
        assert provider.requests[0].model == "test-model"
        assert provider.requests[0].temperature == 0.25
        assert provider.requests[0].max_output_tokens == 100
        assert [tool.name for tool in provider.requests[0].tools] == ["ok"]

    async def test_abnormal_model_finish_fails_run(self) -> None:
        response = ModelResponse(
            message=Message(role="assistant", content="partial"),
            finish_reason=FinishReason.LENGTH,
        )
        runtime = AgentRuntime(
            provider=QueueProvider([response]),
            tools={},
        )

        result = await runtime.run(agent(), "task")

        assert result.reason == TerminationReason.MODEL_FAILED
        assert result.error is not None
        assert result.error.code == "MODEL_ABNORMAL_FINISH"

    async def test_provider_exception_fails_run(self) -> None:
        runtime = AgentRuntime(
            provider=QueueProvider([RuntimeError("offline")]),
            tools={},
        )

        result = await runtime.run(agent(), "task")

        assert result.reason == TerminationReason.MODEL_FAILED
        assert result.error is not None
        assert result.error.code == "PROVIDER_EXCEPTION"

    async def test_consecutive_failed_tool_steps_stop_run(self) -> None:
        provider = QueueProvider(
            [tool_response("missing"), tool_response("missing")]
        )
        runtime = AgentRuntime(
            provider=provider,
            tools={},
            config=RuntimeConfig(max_consecutive_failures=2),
        )

        result = await runtime.run(agent(), "task")

        assert result.reason == TerminationReason.CONSECUTIVE_FAILURE_LIMIT
        assert len(provider.requests) == 2

    async def test_cancellation_stops_in_flight_model_request(self) -> None:
        provider = BlockingProvider()
        token = CancellationToken()
        runtime = AgentRuntime(
            provider=provider,
            tools={},
            cancellation_token=token,
        )
        run = asyncio.create_task(runtime.run(agent(), "task"))
        await provider.started.wait()

        token.cancel()
        result = await asyncio.wait_for(run, timeout=1)

        assert result.status == RunStatus.CANCELLED
        assert result.reason == TerminationReason.CANCEL_REQUESTED
        assert provider.cancelled

    async def test_asyncio_cancellation_propagates_and_stops_model(self) -> None:
        provider = BlockingProvider()
        runtime = AgentRuntime(provider=provider, tools={})
        run = asyncio.create_task(runtime.run(agent(), "task"))
        await provider.started.wait()

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert provider.cancelled
