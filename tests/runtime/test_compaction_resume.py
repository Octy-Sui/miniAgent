from dataclasses import replace

import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelContextLimitError,
    ModelProviderError,
    ModelResponse,
)
from miniagent.runtime import AgentRuntime
from miniagent.runtime.context import select_compaction_prefix
from miniagent.runtime.events import (
    CompactionAttempt,
    CompactionCompleted,
    CompactionRequested,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.result import RunError
from miniagent.runtime.state import RunLifecycle
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.runtime.test_compaction import PipelineEstimator
from tests.runtime.test_context import summary_plan, summary_response
from tests.runtime.test_runtime import QueueProvider


async def failed_summary(tmp_path, used):
    plan = summary_plan()
    seed = plan.state.model_copy(
        update={
            "lifecycle": RunLifecycle(updated_at=plan.state.lifecycle.updated_at),
            "execution": plan.state.execution.model_copy(update={"cursor": None}),
            "definition": plan.state.definition.model_copy(
                update={
                    "config": plan.state.definition.config.model_copy(
                        update={"compaction_max_retries": 2}
                    )
                }
            ),
        }
    )
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    execution = await RunExecution.start(
        seed,
        RunJournal(
            RunRecorder(run_id=seed.identity.run_id, store=store, recovery_context=None)
        ),
    )
    await execution.begin_step(provider="qwen", model="test")
    await execution.record_model_failure("CONTEXT_CAPACITY_EXCEEDED")
    runtime = AgentRuntime(
        provider=QueueProvider([]),
        tools=ToolRegistry([]),
        run_store=store,
        estimator=PipelineEstimator(),
    )
    plan = select_compaction_prefix(
        execution.state,
        runtime._build_request(execution.state),
        runtime.estimator,
        force=True,
    )
    source = await store.load_latest(seed.identity.run_id)
    assert plan is not None and source is not None
    plan = replace(
        plan,
        source_snapshot_version=source.version,
        source_event_sequence=source.event_sequence,
    )
    for number in range(used + 1):
        await execution.begin_compaction_attempt(
            plan, number + 1, branch="forced_compaction"
        )
        await execution.record_compaction_attempt(
            CompactionAttempt(attempt=number + 1, error_code="RETRY"),
            failure=RunError(code="RETRY", message="retryable failure", retryable=True),
        )
    record = await store.get(seed.identity.run_id)
    execution = RunExecution.attach(
        record.state, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    return runtime, execution, store


@pytest.mark.parametrize(("used", "expected_count"), [(0, 2), (1, 1), (2, 0)])
async def test_summary_retry_balance_is_exact(tmp_path, used, expected_count):
    runtime, execution, _store = await failed_summary(tmp_path, used)
    before_steps = execution.state.execution.steps_started
    responses = ([ModelProviderError("retry", retryable=True)] if used == 0 else []) + (
        [
            summary_response(),
            ModelResponse(
                message=AssistantMessage(content="candidate"),
                finish_reason=FinishReason.STOP,
            ),
        ]
        if used < 2
        else []
    )
    provider = QueueProvider(responses)
    runtime.provider = provider
    result = await runtime._drive(execution)
    assert (
        sum(
            not request.tools
            and "summary_schema" in (request.messages[-1].content or "")
            for request in provider.requests
        )
        == expected_count
    )
    assert len(provider.requests) - expected_count == (1 if used < 2 else 0)
    assert execution.state.execution.steps_started == before_steps
    assert execution.state.context.current_version == (1 if used < 2 else None)
    if used == 2:
        assert result.error is not None and result.error.code == "COMPACTION_FAILED"
    else:
        assert result.output == "candidate"
    assert execution.state.execution.phase is None


@pytest.mark.parametrize(("reject_again",), [(False,), (True,)])
async def test_business_context_retry_is_not_refreshed(tmp_path, reject_again):
    runtime, execution, store = await failed_summary(tmp_path, 1)
    runtime.provider = QueueProvider(
        [
            summary_response(),
            ModelContextLimitError("again")
            if reject_again
            else ModelResponse(
                message=AssistantMessage(content="candidate"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    result = await runtime._drive(execution)
    assert len(runtime.provider.requests) == 2
    if reject_again:
        assert (
            result.error is not None
            and result.error.code == "CONTEXT_CAPACITY_EXCEEDED"
        )
    else:
        assert result.output == "candidate"
    record = await store.get(result.run_id)
    assert record.state.execution.request_progress.business_retry_used == 1


@pytest.mark.parametrize(("authorized",), [(False,), (True,)])
async def test_published_summary_and_business_retry_survive_reopen(
    tmp_path, authorized
):
    runtime, execution, store = await failed_summary(tmp_path, 1)
    runtime.provider = QueueProvider([summary_response()])
    await runtime._prepare_request(execution, force=True)
    if authorized:
        await execution.begin_model_retry(context_retry=True)
    record = await store.get(execution.state.identity.run_id)
    execution = RunExecution.attach(
        record.state, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    if authorized:
        await execution.interrupt_operation(unconfirmed=True)
    provider = QueueProvider([ModelContextLimitError("again")])
    runtime.provider = provider
    result = await runtime._drive(execution)
    assert len(provider.requests) == 1
    assert result.error is not None and result.error.code == "CONTEXT_CAPACITY_EXCEEDED"
    assert execution.state.execution.request_progress.business_retry_used == 1
    assert execution.state.context.current_version == 1


async def test_summary_resume_keeps_original_source_and_prefix_with_new_estimator(
    tmp_path,
):
    runtime, execution, store = await failed_summary(tmp_path, 1)
    events = await store.list_events(execution.state.identity.run_id)
    original = next(
        event.payload
        for event in events
        if isinstance(event.payload, CompactionRequested)
    )
    length = len(execution.state.conversation.messages)

    class NewEstimator(PipelineEstimator):
        def estimate(self, request):
            if "summary_schema" in (request.messages[-1].content or ""):
                return 3000
            return 10001 if len(request.messages) >= length else 1000

    runtime.estimator = NewEstimator()
    runtime.provider = QueueProvider([summary_response()])
    await runtime._prepare_request(execution, force=True)
    completed = (await store.list_events(execution.state.identity.run_id))[-1].payload
    assert isinstance(completed, CompactionCompleted)
    assert completed.covered_messages == original.covered_messages
    assert completed.source_snapshot_version == original.source_snapshot_version
