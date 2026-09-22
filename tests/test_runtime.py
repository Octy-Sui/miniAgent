import asyncio
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import orjson
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
from miniagent.policy import PolicyEffect, PolicyEvaluation, RiskLevel
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    EvaluationRequest,
    EvaluationResult,
    Evaluator,
    InvalidRunTransition,
    NewRunEvent,
    RunCursor,
    RunError,
    RunEventType,
    RunResult,
    RunState,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
    transition,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools import Tool, ToolArguments
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


class SuccessfulTool(Tool):
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
        arguments: dict[str, Any],
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
        arguments: dict[str, Any],
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
        message=Message(role="assistant", content=content),
        finish_reason=FinishReason.STOP,
    )


def tool_response(
    name: str = "ok",
    arguments: dict[str, Any] | None = None,
) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="call", name=name, arguments=arguments or {})],
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


def registry(*tools: Tool) -> ToolRegistry:
    return ToolRegistry(tools)


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
    def test_transition_returns_new_state_without_mutating_source(self) -> None:
        state = run_state()

        prepared = transition(state, RunStatus.PREPARING)

        assert prepared is not state
        assert prepared.status is RunStatus.PREPARING
        assert state.status is RunStatus.CREATED
        assert state.version == 0

    def test_valid_lifecycle_reaches_succeeded(self) -> None:
        state = run_state()

        state = transition(state, RunStatus.PREPARING)
        state = transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)
        state.candidate_output = "candidate"
        state = transition(state, RunStatus.EVALUATING)
        state.final_output = "verified"
        state = transition(
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
        state = transition(
            state,
            RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
        )

        result = RunResult.from_state(state)

        assert result.status == RunStatus.CANCELLED
        assert result.started_at is None

    def test_invalid_target_invariants_do_not_mutate_state(self) -> None:
        state = run_state()
        state = transition(state, RunStatus.PREPARING)
        state = transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)

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


class TestRunExecution:
    async def test_failed_step_commit_preserves_state_and_trace(self) -> None:
        state = run_state()
        execution = await RunExecution.start(
            state,
            FailingCompleteStepJournal(
                RunRecorder(
                    run_id=state.run_id,
                    store=None,
                    recovery_context=None,
                )
            ),
        )
        await execution.begin_step(provider="test", model="test-model")
        response = tool_response()
        await execution.record_model_response(
            response,
            None,
            pending_tool_calls=response.message.tool_calls,
        )
        previous_state = execution.state
        state_snapshot = previous_state.model_copy(deep=True)
        previous_trace = execution.trace
        trace_snapshot = deepcopy(previous_trace)

        with pytest.raises(RuntimeError, match="commit failed"):
            await execution.complete_tool_step([])

        assert execution.state is previous_state
        assert execution.state == state_snapshot
        assert execution.trace is previous_trace
        assert execution.trace == trace_snapshot


class TestAgentRuntime:
    async def test_persists_execution_boundaries_to_sqlite(
        self,
        tmp_path: Path,
    ) -> None:
        store = SQLiteRunStore(tmp_path / "runs.sqlite3")
        await store.initialize()
        run_id = uuid4()
        tool = SuccessfulTool()
        runtime = AgentRuntime(
            provider=QueueProvider([tool_response(), final_response()]),
            tools=registry(tool),
            policy_engine=AllowPolicyEngine(),
            run_store=store,
        )

        result = await runtime.run(
            agent(allowed_tools=["ok"]),
            "task",
            run_id=run_id,
        )

        record = await store.get(run_id)
        events = await store.list_events(run_id)
        assert record.state.status is RunStatus.SUCCEEDED
        assert record.state.final_output == result.output
        assert [event.sequence for event in events] == list(
            range(1, len(events) + 1)
        )
        assert {event.type for event in events} >= {
            RunEventType.RUN_STARTED,
            RunEventType.MODEL_REQUESTED,
            RunEventType.MODEL_RESPONDED,
            RunEventType.TOOL_START,
            RunEventType.TOOL_END,
            RunEventType.TOOL_EXECUTION_START,
            RunEventType.TOOL_EXECUTION_END,
            RunEventType.RUN_FINISHED,
        }
        event_types = [event.type for event in events]
        assert event_types.index(RunEventType.TOOL_START) < event_types.index(
            RunEventType.TOOL_EXECUTION_START
        )
        assert event_types.index(
            RunEventType.TOOL_EXECUTION_START
        ) < event_types.index(RunEventType.TOOL_EXECUTION_END)
        assert event_types.index(RunEventType.TOOL_EXECUTION_END) < event_types.index(
            RunEventType.TOOL_END
        )
        assert not {
            RunEventType.STATE_TRANSITIONED,
            RunEventType.SNAPSHOT_CREATED,
            RunEventType.POLICY_DECIDED,
        } & set(event_types)
        tool_start = next(
            event for event in events if event.type is RunEventType.TOOL_START
        )
        tool_end = next(
            event for event in events if event.type is RunEventType.TOOL_END
        )
        execution_start = next(
            event
            for event in events
            if event.type is RunEventType.TOOL_EXECUTION_START
        )
        execution_end = next(
            event
            for event in events
            if event.type is RunEventType.TOOL_EXECUTION_END
        )
        step_start = max(
            (
                event
                for event in events
                if event.type is RunEventType.STEP_STARTED
                and event.sequence < tool_start.sequence
            ),
            key=lambda event: event.sequence,
        )
        assert tool_start.span_id == tool_end.span_id
        assert tool_start.parent_span_id == tool_end.parent_span_id
        assert tool_start.parent_span_id == step_start.span_id
        assert execution_start.span_id == execution_end.span_id
        assert execution_start.span_id != tool_start.span_id
        assert execution_start.parent_span_id == tool_start.span_id
        assert execution_end.parent_span_id == tool_start.span_id
        active_step_number = None
        for event in events:
            if event.type is RunEventType.STEP_STARTED:
                assert active_step_number is None
                active_step_number = event.step_number
            if active_step_number is not None:
                assert event.step_number == active_step_number
            if event.type is RunEventType.STEP_COMPLETED:
                active_step_number = None
        assert active_step_number is None
        assert await store.load_latest(run_id) is not None

    async def test_invalid_tool_arguments_are_returned_without_execution(
        self,
    ) -> None:
        tool = SuccessfulTool()
        provider = QueueProvider(
            [tool_response(arguments={"unexpected": True}), final_response()]
        )
        runtime = AgentRuntime(provider=provider, tools=registry(tool))

        assert (
            await runtime.run(agent(allowed_tools=["ok"]), "task")
        ).status is RunStatus.SUCCEEDED
        assert tool.executions == 0
        message = provider.requests[1].messages[-1]
        assert message.content is not None
        payload = orjson.loads(message.content)
        assert payload["status"] == "error"
        assert payload["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
        assert payload["error"]["issues"][0]["location"] == ["unexpected"]

    async def test_mismatched_tool_result_is_returned_as_structured_error(
        self,
    ) -> None:
        provider = QueueProvider([tool_response(), final_response()])
        runtime = AgentRuntime(
            provider=provider,
            tools=registry(MismatchedResultTool()),
            policy_engine=AllowPolicyEngine(),
        )

        assert (
            await runtime.run(agent(allowed_tools=["ok"]), "task")
        ).status is RunStatus.SUCCEEDED
        message = provider.requests[1].messages[-1]
        assert message.content is not None
        assert orjson.loads(message.content)["error"]["code"] == (
            "INVALID_TOOL_RESULT"
        )

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
            tools=registry(SuccessfulTool()),
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

    async def test_model_usage_accumulates_without_pricing(self) -> None:
        response = final_response()
        response.usage = Usage(
            input_tokens=80,
            cached_input_tokens=20,
            output_tokens=10,
            reasoning_output_tokens=5,
            total_tokens=90,
        )
        runtime = AgentRuntime(
            provider=QueueProvider([response]),
            tools=registry(),
        )

        result = await runtime.run(agent(), "task")

        assert result.usage.input_tokens == 80
        assert result.usage.cached_input_tokens == 20
        assert result.usage.output_tokens == 10
        assert result.usage.reasoning_output_tokens == 5
        assert result.usage.total_tokens == 90
        assert result.usage.cost_usd == Decimal(0)

    async def test_model_stop_is_evaluated_before_run_succeeds(self) -> None:
        evaluator = RecordingEvaluator(
            EvaluationResult(passed=True, output="verified output")
        )
        runtime = AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools=registry(),
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
            tools=registry(),
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
            tools=registry(SuccessfulTool()),
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
            tools=registry(),
        )

        result = await runtime.run(agent(), "task")

        assert result.reason == TerminationReason.MODEL_FAILED
        assert result.error is not None
        assert result.error.code == "MODEL_ABNORMAL_FINISH"

    async def test_provider_exception_fails_run(self) -> None:
        runtime = AgentRuntime(
            provider=QueueProvider([RuntimeError("offline")]),
            tools=registry(),
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
            tools=registry(),
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
            tools=registry(),
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
        runtime = AgentRuntime(provider=provider, tools=registry())
        run = asyncio.create_task(runtime.run(agent(), "task"))
        await provider.started.wait()

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert provider.cancelled
