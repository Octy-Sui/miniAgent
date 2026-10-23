import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelContextLimitError,
    ModelProviderError,
    ModelResponse,
    ModelUsage,
)
from miniagent.runtime.events import (
    CompactionCompleted,
    CompactionRequested,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from tests.support.runtime.compaction import PipelineEstimator
from tests.support.runtime.compaction_resume import failed_summary
from tests.support.runtime.context import summary_response
from tests.support.runtime.runtime import QueueProvider
from tests.support.trajectory.fixtures import request_for


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
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
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
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
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
        await execution.begin_model_retry(
            context_retry=True, request=request_for(execution.state)
        )
    record = await store.get(execution.state.identity.run_id)
    execution = RunExecution.attach(
        record,
        RunJournal.attach(RunRecorder.attach(store, record), []),
        artifact_store=execution.artifact_store,
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
