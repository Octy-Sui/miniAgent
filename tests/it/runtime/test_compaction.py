import sqlite3
from dataclasses import replace
from uuid import uuid4

import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.runtime.context import validate_compaction_record
from miniagent.runtime.execution import ContinueRun
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import CandidateDisposition
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.runtime.state import ContextProgress
from miniagent.storage.sqlite import SQLiteRunStore
from tests.support.runtime.compaction import (
    PipelineEstimator,
    journal_candidate,
    pipeline,
)
from tests.support.runtime.context import summary_plan, summary_response


async def test_compaction_commit_is_immediate_and_reloadable(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    result = await journal.compact(state, event)
    assert result.snapshot.version == 2
    assert result.snapshot.snapshot_id == event.payload.new_snapshot_id
    assert result.snapshot.event_sequence == event.payload.event_sequence
    assert (await store.get(state.identity.run_id)).state.context.current_version == 1
    assert (await store.load_version(state.identity.run_id, 1)).state == plan.state
    assert await store.load_version(state.identity.run_id, 2) == result.snapshot
    await validate_compaction_record(await store.get(state.identity.run_id), store)


async def test_compaction_commit_conflict_is_atomic(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    confirmed = await store.save(
        NewRunSnapshot(
            run_id=state.identity.run_id, state=plan.state, event_sequence=1
        ),
        expected_version=1,
    )
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert await store.load_latest(state.identity.run_id) == confirmed
    assert len(await store.list_events(state.identity.run_id)) == 1


async def test_compaction_transaction_failure_is_atomic(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_snapshot BEFORE INSERT ON run_snapshots WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END"
        )
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert len(await store.list_events(state.identity.run_id)) == 1
    assert (await store.load_latest(state.identity.run_id)).version == 1


@pytest.mark.parametrize(
    ("change",), [("current",), ("previous",), ("snapshot_id",), ("range",), ("hash",)]
)
async def test_corrupt_compaction_record_rejected(tmp_path, change) -> None:
    store, journal, _plan, state, event = await journal_candidate(tmp_path)
    await journal.compact(state, event)
    import json

    with sqlite3.connect(store.path) as connection:
        if change in ("current", "hash"):
            data = json.loads(
                connection.execute("SELECT state_json FROM runs").fetchone()[0]
            )
            if change == "current":
                data["context"]["current_version"] = 99
            else:
                data["conversation"]["messages"][2]["content"] = "tampered summary"
            connection.execute("UPDATE runs SET state_json = ?", (json.dumps(data),))
        else:
            connection.execute("DROP TRIGGER run_events_no_update")
            data = json.loads(
                connection.execute(
                    "SELECT payload_json FROM run_events WHERE type='compaction_completed'"
                ).fetchone()[0]
            )
            if change == "previous":
                data["previous_version"] = 99
            if change == "snapshot_id":
                data["new_snapshot_id"] = str(uuid4())
            if change == "range":
                data["covered_messages"] = [2, 12]
            connection.execute(
                "UPDATE run_events SET payload_json=? WHERE type='compaction_completed'",
                (json.dumps(data),),
            )
    with pytest.raises(ValueError, match="compaction"):
        await validate_compaction_record(await store.get(state.identity.run_id), store)


async def test_normal_append_preserves_context_progress(tmp_path) -> None:
    from miniagent.models.protocol import FinishReason, ModelResponse
    from miniagent.runtime.transitions import accept_model_response

    _store, journal, _plan, state, event = await journal_candidate(tmp_path)
    await journal.compact(state, event)
    advanced = accept_model_response(
        state,
        ModelResponse(
            message=AssistantMessage(content="next"), finish_reason=FinishReason.STOP
        ),
        None,
        disposition=CandidateDisposition(text="next"),
    )
    assert advanced.context == ContextProgress(current_version=1)
    assert advanced.conversation.messages[:-1] == state.conversation.messages


async def test_compaction_rejects_inconsistent_step_metadata_atomically(
    tmp_path,
) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    event = event.model_copy(
        update={"payload": event.payload.model_copy(update={"covered_steps": (1, 2)})}
    )
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert len(await store.list_events(state.identity.run_id)) == 1


async def test_pre_step_compaction_counts_calls_and_publishes_snapshot(
    tmp_path,
) -> None:
    from miniagent.runtime.execution import RunTermination

    runtime, execution, provider, store = await pipeline(tmp_path)
    request = await runtime._prepare_request(execution)
    assert not isinstance(request, RunTermination)
    assert execution.state.execution.steps_started == 6
    assert execution.state.usage.model_requests == 1
    assert len(provider.requests) == 1
    assert provider.requests[0].tools == ()
    assert (await store.load_latest(execution.state.identity.run_id)).version == 2
    assert execution.state.context.current_version == 1
    assert request.messages == execution.state.conversation.messages
    await validate_compaction_record(
        await store.get(execution.state.identity.run_id), store
    )


@pytest.mark.parametrize(("old", "terminates"), [(7000, False), (10001, True)])
async def test_summary_input_over_budget_safe_and_unsafe_old_request(
    tmp_path, old, terminates
) -> None:
    from miniagent.runtime.execution import RunTermination

    runtime, execution, provider, store = await pipeline(
        tmp_path, old=old, summary=9477
    )
    outcome = await runtime._prepare_request(execution)
    assert isinstance(outcome, RunTermination) is terminates
    if terminates:
        assert outcome.error.code == "COMPACTION_INPUT_TOO_LARGE"
    assert provider.requests == []
    assert execution.state.execution.steps_started == 6
    assert execution.state.usage.model_requests == 0
    assert execution.state.context.current_version is None
    assert (await store.load_latest(execution.state.identity.run_id)).version == 1
    types = [
        event.type.value
        for event in await store.list_events(execution.state.identity.run_id)
    ]
    assert types == ["run_started", "compaction_failed"]


@pytest.mark.parametrize(
    ("old", "candidate", "published", "terminates"),
    [
        (7000, 5000, True, False),
        (7000, 5001, False, False),
        (10001, 10000, True, False),
        (10001, 10001, False, True),
    ],
)
async def test_candidate_budget_boundaries(
    tmp_path, old, candidate, published, terminates
) -> None:
    from miniagent.runtime.execution import RunTermination

    runtime, execution, provider, store = await pipeline(
        tmp_path, old=old, candidate=candidate
    )
    outcome = await runtime._prepare_request(execution)
    assert isinstance(outcome, RunTermination) is terminates
    assert (execution.state.context.current_version == 1) is published
    assert len(provider.requests) == 1
    assert (await store.load_latest(execution.state.identity.run_id)).version == (
        2 if published else 1
    )


async def test_rejected_summary_usage_and_price_are_counted(tmp_path) -> None:
    from decimal import Decimal

    from miniagent.metering.pricing import ModelPrice, PriceConfig
    from miniagent.models.protocol import ModelUsage

    response = summary_response(current_goal="").model_copy(
        update={
            "usage": ModelUsage(
                input_tokens=10, cached_input_tokens=3, output_tokens=2, total_tokens=12
            ),
            "provider_request_id": "summary-request",
        }
    )
    runtime, execution, _, store = await pipeline(tmp_path, old=7000, response=response)
    runtime.pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="qwen",
                model="test",
                input_per_token=Decimal(1),
                cached_input_per_token=Decimal(".5"),
                output_per_token=Decimal(2),
            )
        ]
    )
    await runtime._prepare_request(execution)
    assert execution.state.usage.input_tokens == 10
    assert execution.state.usage.cached_input_tokens == 3
    assert execution.state.usage.output_tokens == 2
    assert execution.state.usage.cost_usd == Decimal("12.5")
    failed = (await store.list_events(execution.state.identity.run_id))[-1].payload
    assert failed.attempts[0].provider_request_id == "summary-request"
    assert failed.attempts[0].usage.total_tokens == 12
    assert execution.state.context.current_version is None


async def test_missing_summary_usage_stays_unknown(tmp_path) -> None:
    runtime, execution, _, store = await pipeline(
        tmp_path, old=7000, response=summary_response(current_goal="")
    )
    await runtime._prepare_request(execution)
    failed = (await store.list_events(execution.state.identity.run_id))[-1].payload
    assert failed.attempts[0].usage is None


async def test_compaction_continues_same_run_before_business_step(tmp_path) -> None:
    from miniagent.agents import AgentSpec
    from miniagent.models.config import GenerationOptions, ModelTarget
    from miniagent.models.protocol import FinishReason, ModelResponse
    from miniagent.runtime import AgentRuntime, RunStatus, RuntimeConfig
    from miniagent.tools.contracts import ToolCall
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import (
        AllowPolicyEngine,
        QueueProvider,
        SuccessfulTool,
    )

    tool = SuccessfulTool()
    tool_response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(ToolCall(id="reused", name="ok", arguments={}),)
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    provider = QueueProvider(
        [tool_response] * 4
        + [
            summary_response(),
            ModelResponse(
                message=AssistantMessage(content="done"),
                finish_reason=FinishReason.STOP,
            ),
        ]
    )
    store = SQLiteRunStore(tmp_path / "integration.sqlite3")
    await store.initialize()
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        policy_engine=AllowPolicyEngine(),
        run_store=store,
        estimator=PipelineEstimator(),
        config=RuntimeConfig(context_window_tokens=12000),
    )
    result = await runtime.run(
        AgentSpec(
            name="test",
            system_prompt="fixed",
            model=ModelTarget(provider="qwen", model="test"),
            generation=GenerationOptions(max_output_tokens=976),
            allowed_tools=("ok",),
        ),
        "task",
    )
    assert result.status is RunStatus.SUCCEEDED
    assert result.usage.model_requests == 6
    assert result.usage.tool_calls == 4
    assert tool.executions == 4
    assert provider.requests[4].tools == ()
    assert len(provider.requests[5].tools) == 1
    record = await store.get(result.run_id)
    assert record.state.execution.steps_started == 5
    assert record.state.context.current_version == 1
    await validate_compaction_record(record, store)


@pytest.mark.parametrize(("kind",), [("transient",), ("timeout",)])
async def test_summary_retries_are_bounded_without_new_steps(tmp_path, kind) -> None:
    import asyncio

    from miniagent.models.protocol import ModelProviderError
    from miniagent.runtime.execution import RunTermination

    runtime, execution, provider, store = await pipeline(tmp_path)
    if kind == "transient":
        provider.responses = [ModelProviderError("temporary", retryable=True)] * 3
    else:

        class SlowProvider:
            def __init__(self):
                self.requests = []

            async def generate(self, request):
                self.requests.append(request)
                await asyncio.Event().wait()

        provider = SlowProvider()
        runtime.provider = provider
        execution.state = execution.state.model_copy(
            update={
                "definition": execution.state.definition.model_copy(
                    update={
                        "config": execution.state.definition.config.model_copy(
                            update={"compaction_timeout_seconds": 0.01}
                        )
                    }
                )
            }
        )
    outcome = await runtime._prepare_request(execution)
    assert isinstance(outcome, RunTermination)
    assert len(provider.requests) == 2
    assert execution.state.execution.steps_started == 6
    assert execution.state.usage.model_requests == 2
    assert execution.state.context.current_version is None
    failed = (await store.list_events(execution.state.identity.run_id))[-1].payload
    assert len(failed.attempts) == 2
    assert all(attempt.usage is None for attempt in failed.attempts)


async def test_nonrecoverable_summary_error_is_not_retried(tmp_path) -> None:
    runtime, execution, provider, _store = await pipeline(tmp_path, old=7000)
    provider.responses = [ValueError("not recoverable")]
    outcome = await runtime._prepare_request(execution)
    assert outcome.messages == execution.state.conversation.messages
    assert len(provider.requests) == 1
    assert execution.state.context.current_version is None


async def test_cancellation_stops_summary_without_retry(tmp_path) -> None:
    import asyncio

    from miniagent.runtime.cancellation import CancellationRequested
    from tests.support.runtime.runtime import BlockingProvider

    runtime, execution, _, store = await pipeline(tmp_path)
    blocking = BlockingProvider()
    runtime.provider = blocking
    task = asyncio.create_task(runtime._prepare_request(execution))
    await blocking.started.wait()
    runtime.cancellation_token.cancel()
    with pytest.raises(CancellationRequested):
        await task
    assert blocking.cancelled
    assert execution.state.usage.model_requests == 1
    assert execution.state.execution.steps_started == 6
    failed = (await store.list_events(execution.state.identity.run_id))[-1].payload
    assert failed.attempts[0].usage is None


@pytest.mark.parametrize(("second_rejects",), [(False,), (True,)])
async def test_context_limit_retries_same_step_once(tmp_path, second_rejects) -> None:
    from miniagent.models.protocol import (
        FinishReason,
        ModelContextLimitError,
        ModelResponse,
    )
    from miniagent.runtime.execution import ContinueRun, TerminateRun

    runtime, execution, provider, store = await pipeline(tmp_path, old=1000)
    provider.responses = [
        ModelContextLimitError("context rejected"),
        summary_response(),
        ModelContextLimitError("again")
        if second_rejects
        else ModelResponse(
            message=AssistantMessage(content="done"), finish_reason=FinishReason.STOP
        ),
    ]
    outcome = await runtime._request_model(
        execution, execution.state.definition.agent_spec
    )
    assert isinstance(outcome, TerminateRun if second_rejects else ContinueRun)
    assert len(provider.requests) == 3
    assert execution.state.execution.steps_started == 7
    assert execution.state.usage.model_requests == 3
    assert execution.state.usage.tool_calls == 0
    assert execution.state.context.current_version == 1
    events = await store.list_events(execution.state.identity.run_id)
    assert sum(event.type.value == "step_started" for event in events) == 1
    assert sum(event.type.value == "model_requested" for event in events) == 2


@pytest.mark.parametrize(
    ("mutation",), [("cleared_version",), ("retained_message",), ("retained_range",)]
)
async def test_reload_rejects_rewritten_committed_history(tmp_path, mutation) -> None:
    import json

    store, journal, _plan, state, event = await journal_candidate(tmp_path)
    await journal.compact(state, event)
    with sqlite3.connect(store.path) as connection:
        data = json.loads(
            connection.execute("SELECT state_json FROM runs").fetchone()[0]
        )
        if mutation == "cleared_version":
            data["context"]["current_version"] = None
        if mutation == "retained_message":
            data["conversation"]["messages"][4]["content"] = "rewritten tool result"
        if mutation == "retained_range":
            data["conversation"]["completed_steps"][0]["message_start"] = 0
        connection.execute("UPDATE runs SET state_json=?", (json.dumps(data),))
    with pytest.raises(ValueError, match="compaction"):
        await validate_compaction_record(await store.get(state.identity.run_id), store)


async def test_cancel_returned_summary_before_publish_or_business_step(
    tmp_path,
) -> None:
    from miniagent.models.protocol import ModelUsage
    from miniagent.runtime.cancellation import CancellationRequested

    runtime, execution, _provider, store = await pipeline(tmp_path)

    class ReturningCancellationProvider:
        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            runtime.cancellation_token.cancel()
            return summary_response().model_copy(
                update={
                    "usage": ModelUsage(
                        input_tokens=10, output_tokens=2, total_tokens=12
                    )
                }
            )

    cancelling = ReturningCancellationProvider()
    runtime.provider = cancelling
    with pytest.raises(CancellationRequested):
        await runtime._request_model(execution, execution.state.definition.agent_spec)
    assert execution.state.execution.steps_started == 6
    assert execution.state.usage.model_requests == 1
    assert execution.state.usage.total_tokens == 12
    assert execution.state.context.current_version is None
    assert len(cancelling.requests) == 1
    assert (await store.load_latest(execution.state.identity.run_id)).version == 1
    events = await store.list_events(execution.state.identity.run_id)
    assert all(
        event.type.value not in ("step_started", "compaction_completed")
        for event in events
    )


@pytest.mark.parametrize(("unsafe",), [(False,), (True,)])
async def test_real_summary_capacity_precheck_preserves_safe_business_only(
    tmp_path, unsafe
) -> None:

    from miniagent.runtime import AgentRuntime
    from miniagent.runtime.context import (
        QwenPromptTokenEstimator,
        build_summary_request,
        select_compaction_prefix,
    )
    from miniagent.runtime.execution import (
        RunExecution,
        TerminateRun,
    )
    from miniagent.runtime.state import RunDefinition, RunLifecycle
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import (
        QueueProvider,
        SuccessfulTool,
        final_response,
    )

    class DescribedTool(SuccessfulTool):
        @property
        def definition(self):
            return super().definition.model_copy(
                update={"description": "schema context " * (900 if unsafe else 1)}
            )

    registry = ToolRegistry([DescribedTool()])
    original = summary_plan().state
    agent = original.definition.agent_spec.model_copy(update={"allowed_tools": ("ok",)})
    definition = RunDefinition(
        agent_spec=agent,
        config=original.definition.config,
        tool_definitions=tuple(registry.definitions(agent.allowed_tools)),
        tool_contract_metadata=(registry.contract_metadata("ok"),),
    )
    state = original.model_copy(update={"definition": definition})
    estimator = QwenPromptTokenEstimator()

    def sized_state(size):
        messages = list(state.conversation.messages)
        messages[3] = messages[3].model_copy(update={"content": "x" * size})
        return state.model_copy(
            update={
                "conversation": state.conversation.model_copy(
                    update={"messages": tuple(messages)}
                )
            }
        )

    def summary_total(size):
        candidate = sized_state(size)
        request = request_for_state(candidate)
        plan = select_compaction_prefix(candidate, request, estimator, force=True)
        assert plan is not None
        plan = replace(plan, source_snapshot_version=1, source_event_sequence=1)
        return (
            estimator.estimate(build_summary_request(candidate, plan))
            + plan.budget.summary_reserve
        )

    def request_for_state(candidate):
        from miniagent.models.protocol import ModelRequest

        return ModelRequest(
            model="test",
            messages=candidate.conversation.messages,
            tools=definition.tool_definitions,
            generation=agent.generation,
        )

    low, high = 0, 35000
    while low < high:
        middle = (low + high) // 2
        if summary_total(middle) < 10977:
            low = middle + 1
        else:
            high = middle
    state = sized_state(low)
    assert summary_total(low) == 10977
    old_tokens = estimator.estimate(request_for_state(state))
    assert (old_tokens > 10000) is unsafe
    store = SQLiteRunStore(tmp_path / "real-capacity.sqlite3")
    await store.initialize()
    state = state.model_copy(
        update={
            "lifecycle": RunLifecycle(updated_at=state.lifecycle.updated_at),
            "execution": state.execution.model_copy(update={"cursor": None}),
        }
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=state.identity.run_id, store=store, recovery_context=None
            )
        ),
    )
    provider = QueueProvider([final_response()])
    runtime = AgentRuntime(
        provider=provider, tools=registry, estimator=estimator, run_store=store
    )
    outcome = await runtime._request_model(execution, agent)
    assert isinstance(outcome, TerminateRun if unsafe else ContinueRun)
    if unsafe:
        assert isinstance(outcome, TerminateRun)
        assert outcome.termination.error is not None
        assert outcome.termination.error.code == "COMPACTION_INPUT_TOO_LARGE"
    assert len(provider.requests) == (0 if unsafe else 1)
    assert execution.state.usage.model_requests == (0 if unsafe else 1)
    assert execution.state.context.current_version is None
    snapshot = await store.load_latest(state.identity.run_id)
    assert snapshot is not None
    assert snapshot.version == 1
    events = await store.list_events(state.identity.run_id)
    assert sum(event.type.value == "compaction_failed" for event in events) == 1
    assert all(event.type.value != "compaction_requested" for event in events)


async def test_repeated_compaction_retains_prior_source_chain(tmp_path) -> None:
    from miniagent.agents import AgentSpec
    from miniagent.models.config import GenerationOptions, ModelTarget
    from miniagent.runtime import AgentRuntime, RunStatus, RuntimeConfig
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import (
        AllowPolicyEngine,
        QueueProvider,
        SuccessfulTool,
        final_response,
        tool_response,
    )

    class GrowingEstimator:
        def estimate(self, request):
            if "summary_schema" in (request.messages[-1].content or ""):
                return 3000
            return 10001 if len(request.messages) >= 10 else 1000

    run_id = uuid4()
    prior_evidence = {
        "claim": "done",
        "excerpt": "done",
        "source": {
            "kind": "snapshot",
            "run_id": str(run_id),
            "snapshot_version": 5,
            "message_start": 2,
            "message_end": 4,
        },
    }
    provider = QueueProvider(
        [tool_response()] * 4
        + [
            summary_response(),
            tool_response(),
            summary_response(verified_evidence=[prior_evidence]),
            final_response(),
        ]
    )
    store = SQLiteRunStore(tmp_path / "chain.sqlite3")
    await store.initialize()
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([SuccessfulTool()]),
        estimator=GrowingEstimator(),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
        config=RuntimeConfig(context_window_tokens=12000),
    )
    result = await runtime.run(
        AgentSpec(
            name="test",
            system_prompt="fixed",
            model=ModelTarget(provider="qwen", model="test"),
            generation=GenerationOptions(max_output_tokens=976),
            allowed_tools=("ok",),
        ),
        "task",
        run_id=run_id,
    )
    assert result.status is RunStatus.SUCCEEDED
    record = await store.get(run_id)
    assert record.state.context.current_version == 2
    assert record.state.execution.steps_started == 6
    assert result.usage.model_requests == 8
    assert result.usage.tool_calls == 5
    await validate_compaction_record(record, store)
    assert await store.load_version(run_id, 5) is not None
