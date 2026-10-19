import json
import signal
from pathlib import Path

import pytest

from .test_local import origin, pause, stored, successful_finish


@pytest.mark.parametrize(
    "event", ["create", "model_requested", "model_responded", "tool_execution_end"]
)
async def test_committed_boundary_reopens_in_new_process(spawn_worker, event):
    worker = await spawn_worker(fault_event=event)
    barrier = await worker.boundary("create" if event == "create" else "commit")
    record, events = await stored(worker.root)
    assert record.last_event_sequence == barrier["sequence"]
    assert event == "create" or any(e.type.value == event for e in events)
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    resumed = await spawn_worker(
        "resume",
        label="reopened",
        run_id=origin(worker.root)["run_id"],
        gate_finalization=True,
    )
    if event == "model_requested":
        await resumed.boundary("finalization")
        record, _ = await stored(worker.root)
        assert record.state.usage.model_requests == 3
        assert record.state.usage.unconfirmed_model_requests == 1
        assert (
            Path(origin(worker.root)["repository_root"]) / "effects.txt"
        ).read_text().splitlines() == ["c0", "c1", "c2"]
        resumed.release()
        assert (await resumed.finish())[0] == 0
    else:
        await successful_finish(resumed)


@pytest.mark.parametrize("event", ["resume_started", "resume_completed"])
async def test_recovery_boundary_hard_kill_preserves_business_source(
    spawn_worker, event
):
    first = await pause(spawn_worker)
    run_id = origin(first.root)["run_id"]
    worker = await spawn_worker("resume", label="cut", run_id=run_id, fault_event=event)
    barrier = await worker.boundary("commit")
    record, events = await stored(first.root)
    assert record.last_event_sequence == barrier["sequence"] and any(
        e.type.value == event for e in events
    )
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    final = await spawn_worker(
        "resume", label="final", run_id=run_id, gate_finalization=True
    )
    await successful_finish(final)


async def test_unknown_shell_is_rejected_even_before_effect(spawn_worker):
    worker = await spawn_worker(fault_event="tool_execution_start")
    barrier = await worker.boundary("commit")
    record, events = await stored(worker.root)
    assert (
        record.last_event_sequence == barrier["sequence"]
        and events[-1].type.value == "tool_execution_start"
    )
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    wires = (worker.root / "wire.jsonl").read_bytes()
    final = await spawn_worker(
        "resume", label="rejected", run_id=origin(worker.root)["run_id"]
    )
    code, output = await final.finish()
    assert code == 1 and "UNCERTAIN_TOOL_EXECUTION" in output
    assert (worker.root / "wire.jsonl").read_bytes() == wires
    assert (
        Path(origin(worker.root)["repository_root"]) / "effects.txt"
    ).read_text() == ""


@pytest.mark.parametrize("status", ["error", "timeout"])
async def test_confirmed_failure_result_is_not_unknown_shell(spawn_worker, status):
    worker = await spawn_worker(first_status=status, fault_event="tool_execution_end")
    await worker.boundary("commit")
    _, events = await stored(worker.root)
    assert any(e.type.value == "tool_execution_end" for e in events)
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    resumed = await spawn_worker(
        "resume",
        label="confirmed",
        run_id=origin(worker.root)["run_id"],
        gate_finalization=True,
    )
    await successful_finish(resumed)


@pytest.mark.parametrize("action", ["applied_then_error", "not_dispatched_then_error"])
async def test_live_caller_stops_on_uncertain_store_ack(spawn_worker, action):
    worker = await spawn_worker(fault_event="model_requested", fault_action=action)
    barrier = await worker.boundary("commit")
    assert (await worker.finish())[0] == 1
    record, events = await stored(worker.root)
    result = json.loads((worker.root / "result-initial.json").read_text())
    assert result["persistence_error"]
    assert not (worker.root / "wire.jsonl").exists()
    assert sum(e.type.value == "model_requested" for e in events) == (
        action == "applied_then_error"
    )
    assert barrier["sequence"] == (
        record.last_event_sequence if action == "applied_then_error" else None
    )
