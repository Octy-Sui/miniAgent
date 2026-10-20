import asyncio
import signal
import sqlite3
import sys
from uuid import UUID

import pytest

from miniagent.runtime import AgentRuntime, RunStatus
from miniagent.runtime.events import ApprovalResolved
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import QueueProvider
from tests.support.runtime.runtime_resume import completed_protocol


@pytest.mark.parametrize("mode", ["rejected", "expired", "handler_error"])
@pytest.mark.parametrize("sent", [signal.SIGINT, signal.SIGTERM])
async def test_signal_after_approval_termination_keeps_resolution_and_stopping_decision(
    tmp_path, mode, sent
):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.cli.approval_termination_driver",
        str(tmp_path),
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout
        assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
        process.send_signal(sent)
        _, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 128 + sent.value, stderr.decode()
        store = SQLiteRunStore(tmp_path / "runs.sqlite3")
        with sqlite3.connect(store.path) as connection:
            run_id = UUID(connection.execute("select run_id from runs").fetchone()[0])
        record = await store.get(run_id)
        assert record.state.lifecycle.status is RunStatus.SUSPENDED
        assert record.state.approval.pending is None
        events = await store.list_events(run_id)
        resolved = [e for e in events if e.type.value == "approval_resolved"]
        assert len(resolved) == 1
        assert isinstance(resolved[0].payload, ApprovalResolved)
        assert (
            resolved[0].payload.outcome
            == {
                "rejected": "rejected",
                "expired": "expired",
                "handler_error": "invalidated",
            }[mode]
        )
        approval = await store.get_approval(resolved[0].payload.approval_id)
        assert approval.approval.resolution is not None
        execution = RunExecution.attach(
            record, RunJournal.attach(RunRecorder.attach(store, record), [])
        )
        record = await completed_protocol(store, execution)
        provider = QueueProvider([])
        result = await AgentRuntime(
            provider=provider, tools=ToolRegistry([]), run_store=store
        ).resume(record)
        assert result.status is RunStatus.FAILED and provider.requests == []
        assert (
            result.reason.value
            == {
                "rejected": "approval_rejected",
                "expired": "approval_expired",
                "handler_error": "internal_error",
            }[mode]
        )
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
