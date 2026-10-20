import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime.events import CompactionAttemptRecorded, CompactionRequested
from tests.support.runtime.compaction import pipeline
from tests.support.runtime.context import summary_response


@pytest.mark.parametrize(
    "variant", ["valid", "invalid_json", "invalid_source", "over_budget"]
)
async def test_tc_u05_01(tmp_path, variant):
    response = summary_response()
    if variant == "invalid_json":
        response = ModelResponse(
            message=AssistantMessage(content="not json"),
            finish_reason=FinishReason.STOP,
        )
    elif variant == "invalid_source":
        from uuid import uuid4

        response = summary_response(
            verified_evidence=[
                {
                    "claim": "wrong",
                    "excerpt": "wrong",
                    "source": {
                        "kind": "event",
                        "run_id": str(uuid4()),
                        "event_id": str(uuid4()),
                    },
                }
            ]
        )
    runtime, execution, provider, store = await pipeline(
        tmp_path,
        response=response,
        candidate=100000 if variant == "over_budget" else 1000,
    )
    await runtime._prepare_request(execution, force=True)
    events = await store.list_events(execution.state.identity.run_id)
    requested = next(
        event.payload
        for event in events
        if isinstance(event.payload, CompactionRequested)
    )
    attempt = next(
        event.payload
        for event in events
        if isinstance(event.payload, CompactionAttemptRecorded)
    )
    assert requested.request == provider.requests[0]
    assert requested.attempt_id == attempt.attempt_id == attempt.result.attempt_id
    assert attempt.response == response
    assert execution.state.context.current_version == (
        1 if variant == "valid" else None
    )


@pytest.mark.parametrize(
    "variant", ["no_pricing", "missing_usage", "protocol_failure", "no_response"]
)
async def test_tc_u05_05(tmp_path, variant):
    from decimal import Decimal

    from miniagent.metering.pricing import ModelPrice, PriceConfig
    from miniagent.models.protocol import (
        ModelProviderError,
        ModelResponseNormalizationError,
        ModelUsage,
        ProviderResponseEvidence,
    )
    from tests.support.runtime.runtime import QueueProvider

    response = summary_response().model_copy(
        update={"usage": ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)}
    )
    failure = ProviderResponseEvidence(
        content="final summary sentinel",
        finish_reason="stop",
        usage={"total_tokens": -1},
    )
    supplied = response
    if variant == "missing_usage":
        supplied = response.model_copy(update={"usage": None})
    elif variant == "protocol_failure":
        supplied = ModelResponseNormalizationError(
            evidence=failure, code="BAD_PROTOCOL"
        )
    elif variant == "no_response":
        supplied = ModelProviderError("connection unavailable")
    runtime, execution, _provider, store = await pipeline(tmp_path)
    runtime.provider = QueueProvider([supplied])
    if variant != "no_pricing":
        runtime.pricing = PriceConfig(
            models=[
                ModelPrice(
                    provider="qwen",
                    model="test",
                    input_per_token=Decimal("0.000001"),
                    output_per_token=Decimal("0.000002"),
                )
            ]
        )
    await runtime._prepare_request(execution, force=True)
    attempts = [
        event.payload
        for event in await store.list_events(execution.state.identity.run_id)
        if isinstance(event.payload, CompactionAttemptRecorded)
    ]
    assert len(attempts) == 1
    recorded = attempts[0]
    if variant in {"no_pricing", "missing_usage"}:
        assert recorded.response == supplied
        assert execution.state.context.current_version == 1
    else:
        assert recorded.response is None
        assert execution.state.context.current_version is None
    assert recorded.normalization_failure == (
        failure if variant == "protocol_failure" else None
    )
    assert recorded.result.request_cost is None
    assert recorded.result.usage == (
        response.usage if variant == "no_pricing" else None
    )


async def test_tc_u05_03_attempt_commit_survives_failed_publication_and_retry(tmp_path):
    import sqlite3
    from decimal import Decimal

    from miniagent.metering.pricing import ModelPrice, PriceConfig
    from miniagent.models.protocol import ModelUsage
    from miniagent.runtime.execution import RunExecution
    from miniagent.runtime.journal import RunJournal
    from miniagent.runtime.recording import RunPersistenceError, RunRecorder
    from tests.support.runtime.runtime import QueueProvider

    response = summary_response().model_copy(
        update={"usage": ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)}
    )
    runtime, execution, _provider, store = await pipeline(tmp_path, response=response)
    runtime.pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="qwen",
                model="test",
                input_per_token=Decimal("0.000001"),
                output_per_token=Decimal("0.000002"),
            )
        ]
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_publish AFTER INSERT ON run_events WHEN NEW.type='compaction_completed' BEGIN SELECT RAISE(ABORT,'publish fault'); END"
        )
    with pytest.raises(RunPersistenceError):
        await runtime._prepare_request(execution, force=True)
    record = await store.get(execution.state.identity.run_id)
    assert record.state.context.current_version is None
    assert record.state.usage.cost_usd == Decimal("0.000020")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER fail_publish")
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    runtime.provider = QueueProvider([response])
    await runtime._prepare_request(execution, force=True)
    attempts = [
        event.payload
        for event in await store.list_events(record.state.identity.run_id)
        if isinstance(event.payload, CompactionAttemptRecorded)
    ]
    assert len(attempts) == 2
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert all(attempt.response == response for attempt in attempts)
    assert execution.state.context.current_version == 1
    assert execution.state.usage.cost_usd == Decimal("0.000040")


@pytest.mark.parametrize("variant", ["invalid_json", "invalid_source", "over_budget"])
async def test_tc_u05_02_soft_failure_then_later_success(tmp_path, variant):
    from decimal import Decimal
    from uuid import uuid4

    from miniagent.metering.pricing import ModelPrice, PriceConfig
    from miniagent.models.protocol import ModelUsage
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.compaction import PipelineEstimator
    from tests.support.runtime.runtime import (
        QueueProvider,
        SuccessfulTool,
        final_response,
        tool_response,
    )

    invalid = summary_response(current_goal="too large")
    if variant == "invalid_json":
        invalid = ModelResponse(
            message=AssistantMessage(content="invalid json"),
            finish_reason=FinishReason.STOP,
        )
    elif variant == "invalid_source":
        invalid = summary_response(
            verified_evidence=[
                {
                    "claim": "wrong",
                    "excerpt": "wrong",
                    "source": {
                        "kind": "event",
                        "run_id": str(uuid4()),
                        "event_id": str(uuid4()),
                    },
                }
            ]
        )
    usage = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    invalid = invalid.model_copy(update={"usage": usage})
    valid = summary_response().model_copy(update={"usage": usage})
    runtime, execution, _provider, store = await pipeline(tmp_path, old=8500)

    class Estimator(PipelineEstimator):
        def estimate(self, request):
            if len(request.messages) > 2 and "too large" in (
                request.messages[2].content or ""
            ):
                return 100000
            return super().estimate(request)

    runtime.estimator = Estimator(old=8500)
    runtime.provider = QueueProvider(
        [invalid, tool_response(), valid, final_response()]
    )
    runtime.tools = ToolRegistry([SuccessfulTool()])
    runtime.pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="qwen",
                model="test",
                input_per_token=Decimal("0.000001"),
                output_per_token=Decimal("0.000002"),
            )
        ]
    )
    result = await runtime._drive(execution)
    attempts = [
        event.payload
        for event in await store.list_events(result.run_id)
        if isinstance(event.payload, CompactionAttemptRecorded)
    ]
    assert len(attempts) == 2
    assert attempts[0].response == invalid
    assert attempts[1].response == valid
    assert attempts[0].attempt_id != attempts[1].attempt_id
    assert sum(
        attempt.result.request_cost.total_cost for attempt in attempts
    ) == Decimal("0.000040")
    assert execution.state.context.current_version == 1
    assert result.output == "finished"


async def test_tc_u05_04_published_summary_is_not_recharged_on_resume(tmp_path):
    from tests.support.runtime.runtime import QueueProvider, final_response
    from tests.support.runtime.runtime_resume import completed_protocol

    runtime, execution, provider, store = await pipeline(tmp_path)
    await runtime._prepare_request(execution, force=True)
    assert len(provider.requests) == 1
    record = await completed_protocol(store, execution)
    runtime.provider = QueueProvider([final_response("candidate")])
    result = await runtime.resume(record)
    events = await store.list_events(result.run_id)
    attempts = [
        event
        for event in events
        if isinstance(event.payload, CompactionAttemptRecorded)
    ]
    assert len(attempts) == 1
    assert len(runtime.provider.requests) == 1
    assert result.output == "candidate"


async def test_summary_missing_target_price_preserves_response_before_failure(tmp_path):
    from miniagent.metering.pricing import PriceConfig
    from miniagent.models.protocol import ModelUsage

    response = summary_response().model_copy(
        update={"usage": ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)}
    )
    runtime, execution, provider, store = await pipeline(tmp_path, response=response)
    runtime.pricing = PriceConfig(models=[])
    await runtime._prepare_request(execution, force=True)
    recorded = [
        event.payload
        for event in await store.list_events(execution.state.identity.run_id)
        if isinstance(event.payload, CompactionAttemptRecorded)
    ]
    assert len(recorded) == len(provider.requests) == 1
    assert recorded[0].response == response
    assert recorded[0].result.usage == response.usage
    assert recorded[0].result.request_cost is None
    assert recorded[0].result.error_code == "COST_CALCULATION_FAILED"
    assert execution.state.context.current_version is None
    assert execution.state.usage.input_tokens == 10
