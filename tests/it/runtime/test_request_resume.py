from decimal import Decimal

from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
)
from miniagent.runtime.progress import (
    CandidateDisposition,
    TerminateDisposition,
    TerminationDecision,
)
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.request_resume import reopen, started
from tests.support.runtime.runtime import QueueProvider
from tests.support.trajectory.fixtures import request_for


async def test_started_request_retries_same_step_and_counts_unknown_once(
    tmp_path, runtime_persistence
):
    store, execution = await started(tmp_path)
    execution = await reopen(store, execution)
    await execution.interrupt_operation(unconfirmed=True)
    await execution.interrupt_operation(unconfirmed=True)
    response = ModelResponse(
        message=AssistantMessage(content="candidate"),
        finish_reason=FinishReason.STOP,
        usage=ModelUsage(input_tokens=140, output_tokens=30, total_tokens=170),
    )
    provider = QueueProvider([response])
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
    )._drive(execution)
    assert result.usage.model_requests == 2
    assert result.usage.unconfirmed_model_requests == 1
    assert result.usage.input_tokens == 140 and result.usage.output_tokens == 30
    events = await store.list_events(result.run_id)
    assert sum(event.payload.type == RunEventType.STEP_STARTED for event in events) == 1
    assert len(provider.requests) == 1


async def test_committed_candidate_dispatches_without_model_request(
    tmp_path, runtime_persistence
):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="candidate"),
            finish_reason=FinishReason.STOP,
        ),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    execution = await reopen(store, execution)
    provider = QueueProvider([])
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
    )._drive(execution)
    assert result.output == "candidate" and provider.requests == []


async def test_failure_disposition_with_nonempty_text_never_evaluates(
    tmp_path, runtime_persistence
):
    store, execution = await started(tmp_path)
    decision = TerminationDecision(
        status="failed",
        reason=TerminationReason.MODEL_FAILED,
        error=RunError(code="BAD_RESPONSE", message="invalid"),
    )
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="not a candidate"),
            finish_reason=FinishReason.LENGTH,
        ),
        None,
        disposition=TerminateDisposition(decision=decision),
    )
    execution = await reopen(store, execution)
    provider = QueueProvider([])
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
    )._drive(execution)
    assert result.error is not None
    assert result.error.code == "BAD_RESPONSE" and provider.requests == []
    assert result.output is None


async def test_confirmed_usage_preserves_prior_unknown_attempts(
    tmp_path, runtime_persistence
):
    store, execution = await started(tmp_path)
    await execution.interrupt_operation(unconfirmed=True)
    await execution.begin_model_retry(request=request_for(execution.state))
    execution = await reopen(store, execution)
    await execution.interrupt_operation(unconfirmed=True)
    provider = QueueProvider(
        [
            ModelResponse(
                message=AssistantMessage(content="candidate"),
                finish_reason=FinishReason.STOP,
                usage=ModelUsage(input_tokens=140, output_tokens=30, total_tokens=170),
            )
        ]
    )
    prices = PriceConfig(
        models=[
            ModelPrice(
                provider="test",
                model="test-model",
                input_per_token=Decimal("0.000001"),
                output_per_token=Decimal("0.000002"),
            )
        ]
    )
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
        pricing=prices,
    )._drive(execution)
    assert result.usage.unconfirmed_model_requests == 2
    assert result.usage.model_requests == 3
    assert result.usage.cost_usd == Decimal("0.00020")
    assert result.usage.input_tokens == 140 and result.usage.output_tokens == 30
