from decimal import Decimal
from uuid import uuid4

from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    CandidateDisposition,
    TerminateDisposition,
    TerminationDecision,
)
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.runtime.test_runtime import QueueProvider, agent


async def started(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    spec = agent()
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=spec,
        config=RuntimeConfig(),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=state.identity.run_id, store=store, recovery_context=None
            )
        ),
    )
    await execution.begin_step(provider="test", model="test-model")
    return store, execution


async def reopen(store, execution):
    record = await store.get(execution.state.identity.run_id)
    return RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )


async def test_started_request_retries_same_step_and_counts_unknown_once(tmp_path):
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
        provider=provider, tools=ToolRegistry([]), run_store=store
    )._drive(execution)
    assert result.usage.model_requests == 2
    assert result.usage.unconfirmed_model_requests == 1
    assert result.usage.input_tokens == 140 and result.usage.output_tokens == 30
    events = await store.list_events(result.run_id)
    assert sum(event.payload.type == RunEventType.STEP_STARTED for event in events) == 1
    assert len(provider.requests) == 1


async def test_committed_candidate_dispatches_without_model_request(tmp_path):
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
        provider=provider, tools=ToolRegistry([]), run_store=store
    )._drive(execution)
    assert result.output == "candidate" and provider.requests == []


async def test_failure_disposition_with_nonempty_text_never_evaluates(tmp_path):
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
        provider=provider, tools=ToolRegistry([]), run_store=store
    )._drive(execution)
    assert result.error is not None
    assert result.error.code == "BAD_RESPONSE" and provider.requests == []
    assert result.output is None


async def test_confirmed_usage_preserves_prior_unknown_attempts(tmp_path):
    store, execution = await started(tmp_path)
    await execution.interrupt_operation(unconfirmed=True)
    await execution.begin_model_retry()
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
        provider=provider, tools=ToolRegistry([]), run_store=store, pricing=prices
    )._drive(execution)
    assert result.usage.unconfirmed_model_requests == 2
    assert result.usage.model_requests == 3
    assert result.usage.cost_usd == Decimal("0.00020")
    assert result.usage.input_tokens == 140 and result.usage.output_tokens == 30
