import asyncio
from datetime import UTC, datetime
from multiprocessing import Pipe
from uuid import uuid4

import pytest

from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime.events import (
    ApprovalResolved,
    NewRunEvent,
    ResumeStarted,
    RunStarted,
)
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.store_contracts import RunCommit, RunNotFoundError
from miniagent.runtime.transitions import begin_resume
from miniagent.storage.sqlite import SQLiteRunStore
from tests.support.resume_helpers import StoreBoundary, waiting_case
from tests.support.storage.sqlite import make_state


async def test_attach_preserves_sequence_snapshot_and_approval_versions(tmp_path):
    """TC-U4/U9: attach reads existing versions and creates no synthetic history."""
    store, _, request = await waiting_case(tmp_path / "runs.sqlite3")
    record = await store.get(request.run_id)
    before_events = await store.list_events(request.run_id)
    approval = await store.get_approval(request.approval_id)
    recorder = RunRecorder.attach(store, record)
    journal = RunJournal.attach(recorder, [approval])
    assert recorder.record == record
    assert await store.list_events(request.run_id) == before_events
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
    result = await journal.start_resume(
        state,
        [
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
        ],
        approval_resolution=resolution,
    )
    assert result.record.last_event_sequence == record.last_event_sequence + 2
    assert result.record.latest_snapshot_version == record.latest_snapshot_version + 1
    assert result.approvals[0].version == approval.version + 1
    assert result.record.state.approval.pending is None


@pytest.mark.parametrize(
    ("action", "applied"),
    [("applied_then_error", True), ("not_dispatched_then_error", False)],
)
@pytest.mark.parametrize(("operation",), [("create",), ("commit",)])
async def test_store_boundary_distinguishes_actual_commit_outcome(
    tmp_path, action, applied, operation
):
    """F2: caller sees the same error while independent storage proves E versus U."""

    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    state = make_state()
    boundary = StoreBoundary(store, action=action, operation=operation)
    if operation == "commit":
        await store.create(state)
    with pytest.raises(RunPersistenceError, match="boundary"):
        if operation == "create":
            await boundary.create(state)
        else:
            await boundary.commit(
                RunCommit(
                    state=state,
                    events=[
                        NewRunEvent(run_id=state.identity.run_id, payload=RunStarted())
                    ],
                    expected_sequence=0,
                )
            )
    assert boundary.fired
    if operation == "create" and not applied:
        with pytest.raises(RunNotFoundError):
            await store.get(state.identity.run_id)
    else:
        record = await store.get(state.identity.run_id)
        assert record.last_event_sequence == (
            1 if operation == "commit" and applied else 0
        )


async def test_pause_boundary_requires_exact_supervisor_handshake(tmp_path):

    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    state = make_state()
    await store.create(state)
    supervisor, worker = Pipe()
    boundary = StoreBoundary(
        store, action="pause_after_commit", event_type=RunStarted().type, channel=worker
    )
    change = RunCommit(
        state=state,
        events=[NewRunEvent(run_id=state.identity.run_id, payload=RunStarted())],
        expected_sequence=0,
    )
    task = asyncio.create_task(boundary.commit(change))
    try:
        assert await asyncio.to_thread(supervisor.poll, 2.0), (
            "boundary handshake not reached"
        )
        assert supervisor.recv() == {
            "boundary": "commit",
            "action": "pause_after_commit",
            "sequence": 1,
        }
        assert not task.done()
        assert (await store.get(state.identity.run_id)).last_event_sequence == 1
        supervisor.send("continue")
        assert (await asyncio.wait_for(task, 2.0)).record.last_event_sequence == 1
    finally:
        supervisor.close()
        worker.close()
