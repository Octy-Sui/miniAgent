import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime.events import ApprovalResolved, NewRunEvent, ResumeStarted
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.store_contracts import CorruptStoredDataError
from miniagent.runtime.transitions import begin_resume
from miniagent.tools.contracts import ToolResult, ToolStatus
from tests.support.resume_helpers import waiting_case
from tests.support.storage.sqlite import make_state


async def resume_transaction(path, fault=None):
    store, _, request = await waiting_case(path)
    record = await store.get(request.run_id)
    approval = await store.get_approval(request.approval_id)
    if fault:
        with sqlite3.connect(path) as connection:
            table = "approvals" if fault == "db-a" else "runs"
            connection.execute(
                f"CREATE TRIGGER resume_fault AFTER UPDATE ON {table} BEGIN SELECT RAISE(FAIL, 'resume-fixture-{fault}'); END"
            )
    recorder = RunRecorder.attach(store, record)
    journal = RunJournal.attach(recorder, [approval])
    state = begin_resume(
        record.state, attempt_id=uuid4(), source_sequence=record.last_event_sequence
    )
    session = state.recovery.session
    assert session is not None
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.INVALIDATED,
        actor_kind="system",
        actor_id="runtime",
        reason="resume",
        resolved_at=datetime.now(UTC),
    )
    events = [
        NewRunEvent(
            run_id=request.run_id,
            step_number=1,
            span_id="1111111111111111",
            payload=ApprovalResolved(
                call_index=0,
                tool_call_id="call-1",
                tool_name="shell",
                approval_id=request.approval_id,
                outcome="invalidated",
            ),
        ),
        NewRunEvent(
            run_id=request.run_id,
            payload=ResumeStarted(
                resume_attempt_id=session.attempt_id,
                source_sequence=session.source_sequence,
                business_status=session.business_status,
                phase=session.phase_at_start,
            ),
        ),
    ]
    if fault:
        before_events = await store.list_events(request.run_id)
        with pytest.raises(RunPersistenceError) as caught:
            await journal.start_resume(state, events, approval_resolution=resolution)
        assert f"resume-fixture-{fault}" in str(caught.value.__cause__)
        reopened = await store.get(request.run_id)
        assert reopened == record
        assert await store.get_approval(request.approval_id) == approval
        assert await store.list_events(request.run_id) == before_events
        assert recorder.record == record
    else:
        result = await journal.start_resume(
            state, events, approval_resolution=resolution
        )
        assert result.approvals[0].version == approval.version + 1
        assert result.record.state.recovery.resume_attempts == 1
        assert (
            result.record.latest_snapshot_version == record.latest_snapshot_version + 1
        )
        assert await store.get(request.run_id) == result.record


async def test_resume_transaction_rolls_back_after_approval_write(tmp_path):
    """TC-U4-11 / DB-A: prefix writes roll back after an approval trigger fails."""
    await resume_transaction(tmp_path / "runs.sqlite3", "db-a")


async def test_resume_transaction_rolls_back_after_run_update(tmp_path):
    """TC-U4-12 / DB-B: events/approval/snapshot roll back after run update fails."""
    await resume_transaction(tmp_path / "runs.sqlite3", "db-b")


async def test_resume_transaction_success_control(tmp_path):
    """TC-U4-13 / DB-C: control commits state, audit, approval and snapshot together."""
    await resume_transaction(tmp_path / "runs.sqlite3")


@pytest.mark.parametrize(
    ("mutation",),
    [(None,), ("missing",), ("duplicate",), ("run",), ("step",), ("index",), ("id",)],
)
async def test_tool_results_require_exact_business_scope(tmp_path, mutation):
    """TC-U5-14: full results require exact unique coverage of committed calls."""

    path = tmp_path / "runs.sqlite3"
    store, execution, request = await waiting_case(path)
    await execution.resolve_approval(
        ApprovalResolution(
            approval_id=request.approval_id,
            outcome=ApprovalOutcome.APPROVED,
            actor_kind="human",
            actor_id="tester",
            reason="approved",
            resolved_at=datetime.now(UTC),
        )
    )
    result = ToolResult(
        tool_call_id="call-1", status=ToolStatus.SUCCESS, content="persisted result"
    )
    await execution.complete_tool(result)
    if mutation:
        other = make_state()
        await store.create(other)
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute("DROP TRIGGER run_events_no_delete")
            row = connection.execute(
                "SELECT sequence, payload_json FROM run_events WHERE type='tool_end'"
            ).fetchone()
            if mutation == "missing":
                connection.execute(
                    "DELETE FROM run_events WHERE sequence=? AND run_id=?",
                    (row[0], str(request.run_id)),
                )
            elif mutation == "duplicate":
                connection.execute(
                    "INSERT INTO run_events SELECT run_id, sequence+100, ?, schema_version, type, occurred_at, payload_json, artifacts_json, step_number, span_id, parent_span_id FROM run_events WHERE type='tool_end'",
                    (str(uuid4()),),
                )
            elif mutation in ("run", "step"):
                column, value = (
                    ("run_id", str(other.identity.run_id))
                    if mutation == "run"
                    else ("step_number", 2)
                )
                connection.execute(
                    f"UPDATE run_events SET {column}=? WHERE type='tool_end'", (value,)
                )
            else:
                payload = json.loads(row[1])
                if mutation == "index":
                    payload["call_index"] = 1
                else:
                    payload["tool_call_id"] = "wrong"
                    payload["result"]["tool_call_id"] = "wrong"
                connection.execute(
                    "UPDATE run_events SET payload_json=? WHERE type='tool_end'",
                    (json.dumps(payload),),
                )
        with pytest.raises(CorruptStoredDataError, match="tool result|Tool result"):
            await store.list_tool_results(request.run_id, step_number=1)
    else:
        facts = await store.list_tool_results(request.run_id, step_number=1)
        assert len(facts) == 1
        assert (
            facts[0].step_number,
            facts[0].call_index,
            facts[0].tool_call_id,
            facts[0].result,
        ) == (1, 0, "call-1", result)


async def test_unconsumed_approval_uses_execution_history(tmp_path):
    """TC-U9-02: approved permission is consumed by exact execution, not in-memory state."""
    store, execution, request = await waiting_case(tmp_path / "runs.sqlite3")
    assert len(await store.list_unconsumed_approvals(request.run_id)) == 1
    await execution.resolve_approval(
        ApprovalResolution(
            approval_id=request.approval_id,
            outcome=ApprovalOutcome.APPROVED,
            actor_kind="human",
            actor_id="tester",
            reason="approved",
            resolved_at=datetime.now(UTC),
        )
    )
    human_resolution = (
        await store.get_approval(request.approval_id)
    ).approval.resolution
    assert len(await store.list_unconsumed_approvals(request.run_id)) == 1
    await execution.begin_tool_execution()
    assert await store.list_unconsumed_approvals(request.run_id) == []
    assert (
        await store.get_approval(request.approval_id)
    ).approval.resolution == human_resolution
