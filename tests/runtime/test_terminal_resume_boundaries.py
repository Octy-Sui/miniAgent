import sqlite3
from decimal import Decimal
from uuid import UUID

import pytest

from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import AgentRuntime, RunStatus, RuntimeConfig, TerminationReason
from miniagent.runtime.events import RunEventType
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from miniagent.tools.registry import ToolRegistry
from tests.resume_support import StoreBoundary
from tests.runtime.test_compaction_resume import failed_summary
from tests.runtime.test_context import summary_response
from tests.runtime.test_runtime import (
    AllowPolicyEngine,
    QueueProvider,
    agent,
    final_response,
)
from tests.runtime.test_runtime_resume import completed_protocol
from tests.runtime.test_tool_batch_resume import LineTool


async def reopen(store, tools):
    # Reading through a fresh connection and public recovery transactions, never old memory.
    with sqlite3.connect(store.path) as connection:
        run_id = UUID(connection.execute("select run_id from runs").fetchone()[0])
    record = await store.get(run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    record = await completed_protocol(store, execution)
    provider = QueueProvider([final_response("must not be requested")])
    result = await AgentRuntime(provider=provider, tools=tools, run_store=store).resume(
        record
    )
    assert provider.requests == []
    return result


async def test_failed_step_stop_survives_lost_completion_ack(tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    boundary = StoreBoundary(
        store, action="applied_then_error", event_type=RunEventType.STEP_COMPLETED
    )
    tools = ToolRegistry([LineTool(tmp_path / "effects")])
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(
                ToolCall(
                    id="failure",
                    name="line",
                    arguments={"line": "once", "status": "error"},
                ),
            )
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=tools,
        run_store=boundary,
        config=RuntimeConfig(max_steps=5, max_consecutive_failures=1),
    )
    runtime.policy_engine = AllowPolicyEngine()
    with pytest.raises(RunPersistenceError):
        await runtime.run(agent(allowed_tools=["line"]), "task")
    result = await reopen(store, tools)
    assert (
        result.status is RunStatus.FAILED
        and result.reason is TerminationReason.CONSECUTIVE_FAILURE_LIMIT
    )
    assert (tmp_path / "effects").read_text() == "once\n"


@pytest.mark.parametrize("failure", ["provider_exception", "missing_usage"])
async def test_fatal_model_failure_survives_lost_closure_ack(tmp_path, failure):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    boundary = StoreBoundary(
        store,
        action="applied_then_error",
        event_type=RunEventType.OPERATION_INTERRUPTED,
    )
    prices = (
        PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=Decimal(1),
                    output_per_token=Decimal(1),
                )
            ]
        )
        if failure == "missing_usage"
        else None
    )
    response = (
        RuntimeError("offline")
        if failure == "provider_exception"
        else final_response("unpriced")
    )
    runtime = AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([]),
        run_store=boundary,
        pricing=prices,
    )
    with pytest.raises(RunPersistenceError):
        await runtime.run(agent(), "task")
    result = await reopen(store, ToolRegistry([]))
    assert (
        result.status is RunStatus.FAILED
        and result.reason is TerminationReason.MODEL_FAILED
    )
    assert result.error and result.error.code == (
        "PROVIDER_EXCEPTION"
        if failure == "provider_exception"
        else "MODEL_USAGE_MISSING"
    )


async def test_exhausted_context_retry_survives_confirmed_failure_reopen(tmp_path):
    runtime, execution, store = await failed_summary(tmp_path, 1)
    boundary = StoreBoundary(
        store, action="applied_then_error", event_type=RunEventType.MODEL_RESPONDED
    )
    # Publish a summary and consume the original single business retry.
    runtime.provider = QueueProvider([summary_response()])
    await runtime._prepare_request(execution, force=True)
    await execution.begin_model_retry(context_retry=True)
    record = await store.get(execution.state.identity.run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(boundary, record), [])
    )
    with pytest.raises(RunPersistenceError):
        await execution.record_model_failure("CONTEXT_CAPACITY_EXCEEDED")
    result = await reopen(store, ToolRegistry([]))
    assert (
        result.status is RunStatus.FAILED
        and result.error
        and result.error.code == "CONTEXT_CAPACITY_EXCEEDED"
    )
