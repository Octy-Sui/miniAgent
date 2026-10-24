import asyncio
import logging
import sqlite3

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.diagnostics import heartbeat_interval
from miniagent.runtime import AgentRuntime, CancellationToken
from miniagent.runtime.recording import RunPersistenceError
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    BlockingProvider,
    QueueProvider,
    SuccessfulTool,
    agent,
    final_response,
    tool_response,
)


async def test_committed_runtime_events_are_logged_without_model_content(
    tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="miniagent")
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    result = await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    ).run(agent(), "private-task-content")
    names = [record.msg for record in caplog.records]
    assert "run_started" in names
    assert (
        names.index("model_requested")
        < names.index("model_responded")
        < names.index("run_finished")
    )
    assert "private-task-content" not in caplog.text
    finished = next(record for record in caplog.records if record.msg == "run_finished")
    assert finished.fields["run_id"] == str(result.run_id)
    assert finished.fields["status"] == "succeeded"
    assert finished.fields["sequence"] > 0


async def test_actual_model_wait_has_run_context_and_reclaims_heartbeat(
    tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="miniagent")
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    provider = BlockingProvider()
    cancel = CancellationToken()
    token = heartbeat_interval.set(0.005)
    try:
        task = asyncio.create_task(
            AgentRuntime(
                provider=provider,
                tools=ToolRegistry([]),
                cancellation_token=cancel,
                run_store=store,
                artifact_store=FileArtifactStore(tmp_path / "artifacts"),
            ).run(agent(), "issue")
        )
        await provider.started.wait()
        try:
            async with asyncio.timeout(1):
                while not any(
                    record.msg == "operation.waiting" for record in caplog.records
                ):
                    await asyncio.sleep(0.001)
        finally:
            cancel.cancel()
            result = await task
        waiting = next(
            record for record in caplog.records if record.msg == "operation.waiting"
        )
        assert waiting.fields["run_id"] == str(result.run_id)
        assert waiting.fields["step_number"] == 1
        assert waiting.fields["attempt_id"]
        assert provider.cancelled
        assert not any(
            task.get_name().startswith("heartbeat:") for task in asyncio.all_tasks()
        )
    finally:
        heartbeat_interval.reset(token)


async def test_failed_journal_commit_never_announces_committed_success(
    tmp_path, caplog
):
    class UnwritableStore(SQLiteRunStore):
        async def commit(self, change):
            raise sqlite3.OperationalError("disk full")

    caplog.set_level(logging.INFO, logger="miniagent")
    store = UnwritableStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    with pytest.raises(RunPersistenceError, match="Failed to persist"):
        await AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools=ToolRegistry([]),
            run_store=store,
            artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        ).run(agent(), "issue")
    assert not any(
        record.msg in {"run_started", "model_requested", "run_finished"}
        for record in caplog.records
    )


async def test_tool_summary_includes_exit_code_and_duration_without_output(
    tmp_path, caplog
):
    class CommandTool(SuccessfulTool):
        async def execute(self, arguments, *, tool_call_id):
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.SUCCESS,
                data={
                    "exit_code": 0,
                    "stdout": "private output",
                },
                duration_ms=123,
            )

    caplog.set_level(logging.INFO, logger="miniagent")
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    await AgentRuntime(
        provider=QueueProvider([tool_response(), final_response()]),
        tools=ToolRegistry([CommandTool()]),
        policy_engine=AllowPolicyEngine(),
        run_store=store,
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    ).run(agent(allowed_tools=["ok"]), "issue")
    ended = next(record for record in caplog.records if record.msg == "tool_end")
    assert ended.fields["duration_ms"] == 123
    assert ended.fields["exit_code"] == 0
    assert "private output" not in str(ended.fields)
