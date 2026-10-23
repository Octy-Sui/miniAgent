import asyncio
from datetime import UTC, datetime

import pytest

from miniagent.models.protocol import ModelFailure, ModelProviderError
from miniagent.runtime import AgentRuntime, RunStatus, RuntimeConfig
from miniagent.runtime.cancellation import CancellationToken
from miniagent.runtime.events import ModelRequested
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.compaction import pipeline
from tests.support.runtime.context import summary_response
from tests.support.runtime.request_resume import reopen, started
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    SuccessfulTool,
    agent,
    final_response,
    tool_response,
)
from tests.support.trajectory.fixtures import request_for


def fast_config(**kwargs):
    return RuntimeConfig(
        model_retry={
            "connection_delays": [0, 0, 0, 0],
            "server_delays": [0, 0, 0, 0],
            "rate_limit_delays": [0, 0, 0, 0],
            "gateway_delays": [0, 0, 0, 0],
            "jitter_ratio": 0,
            **kwargs,
        }
    )


async def test_transient_failure_retries_same_step_without_replaying_tool(
    runtime_persistence,
):
    tool = SuccessfulTool()
    provider = QueueProvider(
        [
            tool_response(),
            ModelProviderError("offline", retryable=True),
            final_response(),
        ]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        policy_engine=AllowPolicyEngine(),
        config=fast_config(),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(allowed_tools=["ok"]), "task")
    assert result.status is RunStatus.SUCCEEDED
    assert tool.executions == 1
    assert provider.requests[1] == provider.requests[2]
    assert result.usage.model_requests == 3
    assert result.usage.unconfirmed_model_requests == 1
    events = await runtime_persistence.store.list_events(result.run_id)
    requests = [e for e in events if isinstance(e.payload, ModelRequested)]
    assert [e.step_number for e in requests] == [1, 2, 2]
    assert len({e.payload.attempt_id for e in requests}) == 3


async def test_five_consecutive_failures_stop_before_sixth_request(runtime_persistence):
    provider = QueueProvider(
        [ModelProviderError("offline", retryable=True)] * 5 + [final_response()]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        config=fast_config(),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(), "task")
    assert result.status is RunStatus.FAILED
    assert result.error is not None
    assert result.error.code == "MODEL_RETRY_EXHAUSTED"
    assert len(provider.requests) == 5
    assert result.usage.unconfirmed_model_requests == 5


async def test_usage_missing_does_not_stop_tool_or_candidate(runtime_persistence):
    tool = SuccessfulTool()
    provider = QueueProvider(
        [
            tool_response().model_copy(update={"usage": None}),
            final_response().model_copy(update={"usage": None}),
        ]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        policy_engine=AllowPolicyEngine(),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(allowed_tools=["ok"]), "task")
    assert result.status is RunStatus.SUCCEEDED
    assert tool.executions == 1
    assert result.usage.unconfirmed_model_requests == 2
    assert result.usage.model_requests == 2


async def test_cancellation_during_backoff_does_not_send_retry(runtime_persistence):
    token = CancellationToken()
    provider = QueueProvider(
        [ModelProviderError("offline", retryable=True), final_response()]
    )
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        cancellation_token=token,
        config=fast_config(connection_delays=[30, 30, 30, 30]),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    )
    task = asyncio.create_task(runtime.run(agent(), "task"))
    while not provider.requests:
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)
    token.cancel()
    result = await asyncio.wait_for(task, 1)
    assert result.status is RunStatus.CANCELLED
    assert len(provider.requests) == 1


async def test_retry_limit_survives_reopening_journal(tmp_path, runtime_persistence):

    store, execution = await started(
        tmp_path, config=fast_config(max_consecutive_failures=2)
    )
    await execution.interrupt_operation(unconfirmed=True)
    execution = await reopen(store, execution)
    await execution.begin_model_retry(request=request_for(execution.state))
    await execution.interrupt_operation(unconfirmed=True)
    execution = await reopen(store, execution)
    provider = QueueProvider([final_response()])
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=runtime_persistence.artifacts,
    )._drive(execution)
    assert result.error is not None
    assert result.error.code == "MODEL_RETRY_EXHAUSTED"
    assert provider.requests == []
    assert result.usage.unconfirmed_model_requests == 2


async def test_retry_after_beyond_deadline_stops_without_another_attempt(
    runtime_persistence,
):

    provider = QueueProvider(
        [
            ModelProviderError(
                "limited",
                failure=ModelFailure(
                    category="rate_limit",
                    retryable=True,
                    retry_after_seconds=60,
                ),
            ),
            final_response(),
        ]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        config=fast_config(timeout_seconds=1),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(), "task")
    assert result.error is not None
    assert result.error.code == "MODEL_REQUEST_DEADLINE"
    assert len(provider.requests) == 1


async def test_mixed_failures_share_one_limit(runtime_persistence):

    provider = QueueProvider(
        [
            ModelProviderError(
                "temporary",
                failure=ModelFailure(
                    category=category,
                    retryable=True,
                ),
            )
            for category in (
                "rate_limit",
                "server",
                "gateway",
                "connection",
                "read_timeout",
            )
        ]
        + [final_response()]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        config=fast_config(),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(), "task")
    assert result.error is not None
    assert result.error.code == "MODEL_RETRY_EXHAUSTED"
    assert len(provider.requests) == 5


async def test_summary_retry_records_wait_and_failure_metadata(tmp_path):

    runtime, execution, _, store = await pipeline(tmp_path)
    execution.state = execution.state.model_copy(
        update={
            "definition": execution.state.definition.model_copy(
                update={
                    "config": execution.state.definition.config.model_copy(
                        update={"model_retry": fast_config().model_retry}
                    )
                }
            )
        }
    )
    runtime.provider = QueueProvider(
        [
            ModelProviderError(
                "limited",
                failure=ModelFailure(
                    category="rate_limit",
                    retryable=True,
                    retry_after_seconds=0.03,
                ),
            ),
            summary_response().model_copy(update={"usage": final_response().usage}),
        ]
    )
    before = datetime.now(UTC)
    await runtime._prepare_request(execution, force=True)
    assert (datetime.now(UTC) - before).total_seconds() >= 0.03
    assert execution.state.context.current_version == 1
    assert execution.state.usage.unconfirmed_model_requests == 1
    assert len(runtime.provider.requests) == 2
    events = await store.list_events(execution.state.identity.run_id)
    attempts = [
        e.payload for e in events if e.payload.type == "compaction_attempt_recorded"
    ]
    assert attempts[0].failure.category == "rate_limit"


async def test_summary_backoff_survives_reopening_journal(tmp_path):

    runtime, execution, _, store = await pipeline(tmp_path)
    execution.state = execution.state.model_copy(
        update={
            "definition": execution.state.definition.model_copy(
                update={
                    "config": execution.state.definition.config.model_copy(
                        update={
                            "model_retry": fast_config(
                                connection_delays=[0.3]
                            ).model_retry,
                        }
                    ),
                }
            ),
        }
    )
    runtime.provider = QueueProvider([ModelProviderError("offline", retryable=True)])
    pending = asyncio.create_task(runtime._prepare_request(execution, force=True))

    async def saved_backoff():
        while True:
            record = await store.get(execution.state.identity.run_id)
            if record.state.execution.request_progress.summary_retry_at is not None:
                return record.state.execution.request_progress
            await asyncio.sleep(0.001)

    try:
        progress = await asyncio.wait_for(saved_backoff(), 2)
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    reopened_store = SQLiteRunStore(store.path)
    execution = await reopen(reopened_store, execution)
    assert execution.state.execution.request_progress == progress

    class Provider:
        async def generate(self, request):
            assert datetime.now(UTC) >= progress.summary_retry_at
            return summary_response()

    runtime.provider = Provider()
    await runtime._prepare_request(execution, force=True)
    assert execution.state.context.current_version == 1
    events = await reopened_store.list_events(execution.state.identity.run_id)
    attempts = [
        e.payload.result
        for e in events
        if e.payload.type == "compaction_attempt_recorded"
    ]
    assert [attempt.attempt for attempt in attempts] == [1, 2]


async def test_unrepresentable_retry_after_stops_cleanly(runtime_persistence):

    provider = QueueProvider(
        [
            ModelProviderError(
                "limited",
                failure=ModelFailure(
                    category="rate_limit",
                    retryable=True,
                    retry_after_seconds=1e100,
                ),
            )
        ]
    )
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        config=fast_config(),
        run_store=runtime_persistence.store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(), "task")
    assert result.error is not None and result.error.code == "MODEL_REQUEST_DEADLINE"
    assert len(provider.requests) == 1


async def test_first_summary_attempt_obeys_logical_deadline(tmp_path):

    runtime, execution, _, _ = await pipeline(tmp_path)
    execution.state = execution.state.model_copy(
        update={
            "definition": execution.state.definition.model_copy(
                update={
                    "config": execution.state.definition.config.model_copy(
                        update={
                            "model_retry": fast_config(
                                timeout_seconds=0.01
                            ).model_retry,
                        }
                    ),
                }
            ),
        }
    )

    class SlowProvider:
        async def generate(self, request):
            await asyncio.sleep(0.08)
            return summary_response()

    runtime.provider = SlowProvider()
    await runtime._prepare_request(execution, force=True)
    assert execution.state.context.current_version is None
    assert execution.state.usage.unconfirmed_model_requests == 1
