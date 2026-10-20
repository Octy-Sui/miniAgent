from decimal import Decimal

import pytest

from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelResponse,
    ModelResponseNormalizationError,
    ModelUsage,
    ProviderResponseEvidence,
)
from miniagent.runtime import AgentRuntime
from miniagent.runtime.events import ModelRequested, ModelResponded
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import QueueProvider, agent


@pytest.mark.parametrize(
    "variant",
    [
        "accepted",
        "no_pricing",
        "missing_usage",
        "missing_usage_no_pricing",
        "empty_stop",
        "duplicate_calls",
        "unknown_finish",
        "missing_price",
        "normalization",
    ],
)
async def test_tc_u03_03(tmp_path, variant):
    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    calls = (
        (ToolCall(id="same", name="ok", arguments={"sentinel": "arguments"}),) * 2
        if variant == "duplicate_calls"
        else ()
    )
    response = ModelResponse(
        message=AssistantMessage(
            content="" if variant == "empty_stop" else "final sentinel",
            tool_calls=calls,
        ),
        finish_reason=FinishReason.UNKNOWN
        if variant == "unknown_finish"
        else FinishReason.TOOL_CALL
        if calls
        else FinishReason.STOP,
        usage=None if variant.startswith("missing_usage") else usage,
    )
    failure = ProviderResponseEvidence(
        content="final sentinel", finish_reason="stop", usage={"total_tokens": -1}
    )
    provider = QueueProvider(
        [
            ModelResponseNormalizationError(evidence=failure, code="BAD_PROTOCOL")
            if variant == "normalization"
            else response
        ]
    )
    pricing = (
        None
        if variant in {"no_pricing", "missing_usage_no_pricing"}
        else PriceConfig(
            models=[]
            if variant == "missing_price"
            else [
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=Decimal("0.000001"),
                    output_per_token=Decimal("0.000001"),
                )
            ]
        )
    )
    runtime = AgentRuntime(
        provider=provider, tools=ToolRegistry([]), run_store=store, pricing=pricing
    )
    result = await runtime.run(agent(), "task")
    record = await store.get(result.run_id)
    events = await store.list_events(result.run_id)
    requested = next(
        event.payload for event in events if isinstance(event.payload, ModelRequested)
    )
    responded = next(
        event.payload for event in events if isinstance(event.payload, ModelResponded)
    )
    assert requested.request == provider.requests[0]
    assert requested.attempt_id == responded.attempt_id
    assert record.state.execution.active_operation is None
    if variant == "normalization":
        assert responded.normalization_failure == failure
        assert responded.response is None
        assert responded.cost_status == "invalid_usage"
    else:
        assert responded.response == response
        assert responded.finish_reason == response.finish_reason.value
        assert responded.tool_call_count == len(calls)
    if variant.startswith("missing_usage") or variant == "normalization":
        assert record.state.usage.unconfirmed_model_requests == 1
        assert responded.request_cost is None
        assert result.error is not None
    elif variant == "missing_price":
        assert responded.cost_status == "calculation_failed"
        assert result.error is not None
        assert result.error.code == "MODEL_PRICING_UNAVAILABLE"
    elif variant == "no_pricing":
        assert responded.cost_status == "not_configured"
        assert responded.request_cost is None
    else:
        assert responded.request_cost is not None
        assert responded.request_cost.total_cost == Decimal("0.000020")
        assert record.state.usage.cost_usd == Decimal("0.000020")
    if variant not in {"accepted", "no_pricing"}:
        assert result.error is not None
        assert len(provider.requests) == 1


@pytest.mark.parametrize("stage", ["model_requested", "model_responded"])
async def test_tc_u03_02_atomic_sql_failure(tmp_path, stage):
    import sqlite3

    from miniagent.runtime.recording import RunPersistenceError

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"CREATE TRIGGER fail_evidence AFTER INSERT ON run_events WHEN NEW.type='{stage}' BEGIN SELECT RAISE(ABORT, 'evidence transaction fault'); END"
        )
    provider = QueueProvider(
        [
            ModelResponse(
                message=AssistantMessage(content="final sentinel"),
                finish_reason=FinishReason.STOP,
                usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            )
        ]
    )
    runtime = AgentRuntime(provider=provider, tools=ToolRegistry([]), run_store=store)
    from uuid import uuid4

    run_id = uuid4()
    with pytest.raises(RunPersistenceError):
        await runtime.run(agent(), "task", run_id=run_id)
    reopened = SQLiteRunStore(store.path)
    events = await reopened.list_events(run_id)
    record = await reopened.get(run_id)
    assert not any(event.type.value == stage for event in events)
    assert record.state.usage.total_tokens == 0
    assert len(provider.requests) == (0 if stage == "model_requested" else 1)
    assert (record.state.execution.active_operation is None) == (
        stage == "model_requested"
    )


async def test_tc_u03_04_unexpected_calculation_exception_propagates(tmp_path):
    from decimal import Overflow
    from uuid import uuid4

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    provider = QueueProvider(
        [
            ModelResponse(
                message=AssistantMessage(content="final sentinel"),
                finish_reason=FinishReason.STOP,
                usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            )
        ]
    )
    pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="test",
                model="test-model",
                input_per_token=Decimal("1e999999999"),
                output_per_token=Decimal(1),
            )
        ]
    )
    run_id = uuid4()
    with pytest.raises(Overflow):
        await AgentRuntime(
            provider=provider, tools=ToolRegistry([]), run_store=store, pricing=pricing
        ).run(agent(), "task", run_id=run_id)
    events = await store.list_events(run_id)
    assert sum(isinstance(event.payload, ModelRequested) for event in events) == 1
    assert not any(isinstance(event.payload, ModelResponded) for event in events)


async def test_cancelled_response_transaction_drains_actual_commit(
    tmp_path, monkeypatch
):
    import asyncio
    import sqlite3
    from uuid import uuid4

    from miniagent.runtime.recording import RunPersistenceError
    from tests.support.trajectory.barriers import SQLiteWriteBarrier

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    barrier = SQLiteWriteBarrier("model_responded")
    monkeypatch.setattr(sqlite3, "connect", barrier.connector(sqlite3.connect))
    response = ModelResponse(
        message=AssistantMessage(content="committed response"),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
    )
    provider = QueueProvider([response])
    run_id = uuid4()
    task = asyncio.create_task(
        AgentRuntime(provider=provider, tools=ToolRegistry([]), run_store=store).run(
            agent(), "task", run_id=run_id
        )
    )
    try:
        assert await asyncio.to_thread(barrier.entered.wait, 5), (
            "transaction window not reached"
        )
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        barrier.release.set()
    with pytest.raises(RunPersistenceError) as exc:
        await task
    assert isinstance(exc.value.__cause__, asyncio.CancelledError)
    reopened = SQLiteRunStore(store.path)
    record = await reopened.get(run_id)
    events = await reopened.list_events(run_id)
    responses = [
        event.payload for event in events if isinstance(event.payload, ModelResponded)
    ]
    assert len(responses) == 1
    assert responses[0].response == response
    assert record.state.usage.total_tokens == 20
    assert record.state.execution.active_operation is None


async def test_tc_u03_01_request_committed_before_provider_entry(tmp_path):
    from uuid import uuid4

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    run_id = uuid4()
    response = ModelResponse(
        message=AssistantMessage(content="final sentinel"),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
    )

    class Provider:
        async def generate(self, request):
            events = await SQLiteRunStore(store.path).list_events(run_id)
            assert isinstance(events[-1].payload, ModelRequested)
            assert events[-1].payload.request == request
            return response

    result = await AgentRuntime(
        provider=Provider(), tools=ToolRegistry([]), run_store=store
    ).run(agent(), "task", run_id=run_id)
    assert result.output == "final sentinel"


async def test_accepted_tool_response_preserves_evidence_and_charges_once(tmp_path):
    from tests.support.runtime.runtime import AllowPolicyEngine, SuccessfulTool

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    usage = ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20)
    tool_response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="single", name="ok", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
        usage=usage,
    )
    final = ModelResponse(
        message=AssistantMessage(content="done"),
        finish_reason=FinishReason.STOP,
        usage=usage,
    )
    tool = SuccessfulTool()
    provider = QueueProvider([tool_response, final])
    pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="test",
                model="test-model",
                input_per_token=Decimal("0.000001"),
                output_per_token=Decimal("0.000001"),
            )
        ]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        run_store=store,
        pricing=pricing,
        policy_engine=AllowPolicyEngine(),
    ).run(agent(allowed_tools=["ok"]), "task")
    events = await store.list_events(result.run_id)
    responses = [
        event.payload for event in events if isinstance(event.payload, ModelResponded)
    ]
    assert responses[0].response == tool_response
    assert responses[0].request_cost is not None
    assert responses[0].request_cost.total_cost == Decimal("0.000020")
    assert result.usage.cost_usd == Decimal("0.000040")
    assert tool.executions == 1
    assert result.output == "done"
