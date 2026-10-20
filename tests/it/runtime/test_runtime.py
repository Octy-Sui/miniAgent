import asyncio
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import orjson
import pytest

from miniagent.artifacts import ArtifactWrite, FileArtifactStore
from miniagent.evaluation import EvaluationError
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    EvaluationRequest,
    EvaluationResult,
    RunEventType,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
)
from miniagent.runtime.events import ToolEnded
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.progress import ToolsDisposition
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolResult, ToolStatus
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    BlockingProvider,
    FailingCompleteStepJournal,
    MismatchedResultTool,
    QueueProvider,
    RecordingEvaluator,
    SuccessfulTool,
    agent,
    final_response,
    registry,
    run_state,
    tool_response,
)
from tests.support.trajectory.fixtures import request_for


class TestRunExecution:
    async def test_failed_step_commit_preserves_state_and_trace(self) -> None:
        state = run_state()
        execution = await RunExecution.start(
            state,
            FailingCompleteStepJournal(
                RunRecorder(
                    run_id=state.identity.run_id,
                    store=None,
                    recovery_context=None,
                )
            ),
        )
        await execution.begin_step(
            provider="test", model="test-model", request=request_for(execution.state)
        )
        response = tool_response()
        await execution.record_model_response(
            response,
            None,
            disposition=ToolsDisposition(),
        )
        call = response.message.tool_calls[0]
        await execution.begin_tool(
            call_index=0,
            tool_call_id=call.id,
            tool_name=call.name,
        )
        await execution.complete_tool(
            ToolResult(
                tool_call_id=call.id,
                status=ToolStatus.SUCCESS,
                content="ok",
            )
        )
        previous_state = execution.state
        state_snapshot = previous_state.model_copy(deep=True)
        previous_trace = execution.trace
        trace_snapshot = deepcopy(previous_trace)

        with pytest.raises(RuntimeError, match="commit failed"):
            await execution.complete_tool_step()

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
        assert record.state.lifecycle.status is RunStatus.SUCCEEDED
        assert record.state.lifecycle.final_output == result.output
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert {event.type for event in events} >= {
            RunEventType.RUN_STARTED,
            RunEventType.MODEL_REQUESTED,
            RunEventType.MODEL_RESPONDED,
            RunEventType.TOOL_START,
            RunEventType.TOOL_END,
            RunEventType.TOOL_EXECUTION_START,
            RunEventType.TOOL_EXECUTION_END,
            RunEventType.POLICY_DECIDED,
            RunEventType.RUN_FINISHED,
        }
        event_types = [event.type for event in events]
        assert event_types.index(RunEventType.TOOL_START) < event_types.index(
            RunEventType.TOOL_EXECUTION_START
        )
        assert event_types.index(RunEventType.TOOL_EXECUTION_START) < event_types.index(
            RunEventType.TOOL_EXECUTION_END
        )
        assert event_types.index(RunEventType.TOOL_EXECUTION_END) < event_types.index(
            RunEventType.TOOL_END
        )
        tool_start = next(
            event for event in events if event.type is RunEventType.TOOL_START
        )
        tool_end = next(
            event for event in events if event.type is RunEventType.TOOL_END
        )
        assert isinstance(tool_end.payload, ToolEnded)
        assert tool_end.payload.result is not None
        assert tool_end.payload.result.tool_call_id == tool_end.payload.tool_call_id
        execution_start = next(
            event for event in events if event.type is RunEventType.TOOL_EXECUTION_START
        )
        execution_end = next(
            event for event in events if event.type is RunEventType.TOOL_EXECUTION_END
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
        assert orjson.loads(message.content)["error"]["code"] == ("INVALID_TOOL_RESULT")

    async def test_model_usage_and_cost_accumulate_across_steps(self) -> None:
        first = tool_response().model_copy(
            update={
                "usage": ModelUsage(
                    input_tokens=100,
                    cached_input_tokens=40,
                    output_tokens=20,
                    reasoning_output_tokens=5,
                    total_tokens=120,
                )
            }
        )
        second = final_response().model_copy(
            update={
                "usage": ModelUsage(
                    input_tokens=200,
                    cached_input_tokens=50,
                    output_tokens=30,
                    reasoning_output_tokens=10,
                    total_tokens=230,
                )
            }
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
        response = final_response().model_copy(
            update={
                "usage": ModelUsage(
                    input_tokens=80,
                    cached_input_tokens=20,
                    output_tokens=10,
                    reasoning_output_tokens=5,
                    total_tokens=90,
                )
            }
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

    async def test_provider_request_does_not_share_state_message_internals(
        self,
    ) -> None:
        first = tool_response(arguments={"value": "original"})

        class MutatingProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def generate(self, request: ModelRequest) -> ModelResponse:
                self.calls += 1
                if self.calls == 1:
                    return first
                assistant = request.messages[2]
                assert isinstance(assistant, AssistantMessage)
                assistant.tool_calls[0].arguments["value"] = "mutated"
                return final_response()

        runtime = AgentRuntime(
            provider=MutatingProvider(),
            tools=registry(SuccessfulTool()),
        )

        assert (
            await runtime.run(agent(allowed_tools=["ok"]), "task")
        ).status is RunStatus.SUCCEEDED
        assert first.message.tool_calls[0].arguments == {"value": "original"}

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
        assert evaluator.requests[0].candidate.text == "finished"
        assert evaluator.requests[0].run_id == result.run_id

    async def test_failed_evaluation_fails_run(self) -> None:
        evaluator = RecordingEvaluator(
            EvaluationResult(
                passed=False,
                error=EvaluationError(code="TESTS_FAILED", message="Tests failed"),
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

    @pytest.mark.parametrize("passed", [True, False])
    async def test_evaluation_completion_uses_run_child_span(
        self,
        tmp_path: Path,
        passed: bool,
    ) -> None:
        store = SQLiteRunStore(tmp_path / "runs.sqlite3")
        await store.initialize()

        class CheckingEvaluator:
            async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
                events = await store.list_events(request.run_id)
                assert events[-1].type is RunEventType.EVALUATION_START
                assert RunEventType.EVALUATION_COMPLETED not in {
                    event.type for event in events
                }
                return EvaluationResult(passed=passed)

        runtime = AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools=registry(),
            evaluator=CheckingEvaluator(),
            run_store=store,
        )

        result = await runtime.run(agent(), "task")
        events = await store.list_events(result.run_id)
        run_start = next(
            event for event in events if event.type is RunEventType.RUN_STARTED
        )
        evaluation_start = next(
            event for event in events if event.type is RunEventType.EVALUATION_START
        )
        evaluation = next(
            event for event in events if event.type is RunEventType.EVALUATION_COMPLETED
        )
        run_finish = next(
            event for event in events if event.type is RunEventType.RUN_FINISHED
        )

        assert run_start.span_id == run_finish.span_id
        assert evaluation_start.span_id == evaluation.span_id
        assert evaluation_start.parent_span_id == run_start.span_id
        assert evaluation_start.step_number is None
        assert evaluation.span_id != run_start.span_id
        assert evaluation.parent_span_id == run_start.span_id
        assert evaluation.step_number is None
        assert (
            run_start.sequence
            < evaluation_start.sequence
            < evaluation.sequence
            < run_finish.sequence
        )

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
        assert provider.requests[0].generation.temperature == 0.25
        assert provider.requests[0].generation.max_output_tokens == 100
        assert [tool.name for tool in provider.requests[0].tools] == ["ok"]

    async def test_abnormal_model_finish_fails_run(self) -> None:
        response = ModelResponse(
            message=AssistantMessage(content="partial"),
            finish_reason=FinishReason.LENGTH,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
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
        provider = QueueProvider([tool_response("missing"), tool_response("missing")])
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


@pytest.mark.parametrize(("passed",), [(True,), (False,)])
async def test_evaluation_artifacts_commit_with_terminal_state(tmp_path, passed):
    run_id = uuid4()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    report_ref = await artifacts.put(
        ArtifactWrite(run_id=run_id, type="evaluation_report"), b"report"
    )
    store = SQLiteRunStore(tmp_path / "journal.sqlite3")
    await store.initialize()
    runtime = AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=registry(),
        run_store=store,
        evaluator=RecordingEvaluator(
            EvaluationResult(passed=passed, artifacts=[report_ref])
        ),
    )
    result = await runtime.run(agent(), "task", run_id=run_id)
    reopened = SQLiteRunStore(tmp_path / "journal.sqlite3")
    await reopened.initialize()
    events = await reopened.list_events(run_id)
    completed = next(
        event for event in events if event.type is RunEventType.EVALUATION_COMPLETED
    )
    finished = next(
        event for event in events if event.type is RunEventType.RUN_FINISHED
    )
    started = next(event for event in events if event.type is RunEventType.RUN_STARTED)
    snapshot = await reopened.load_latest(run_id)
    assert snapshot is not None
    assert snapshot.state.lifecycle.status is result.status
    assert completed.artifacts == [report_ref]
    assert completed.sequence < finished.sequence
    assert completed.span_id != started.span_id
    assert result.status is (RunStatus.SUCCEEDED if passed else RunStatus.FAILED)
    assert result.reason is (
        TerminationReason.TASK_VERIFIED
        if passed
        else TerminationReason.EVALUATION_FAILED
    )
