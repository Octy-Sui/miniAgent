import asyncio
import hashlib
import json
import signal
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.storage import SQLiteRunStore


def origin(root: Path) -> dict:
    return json.loads((root / "origin.json").read_text())


async def stored(root: Path):
    metadata = origin(root)
    store = SQLiteRunStore(root / "runs.sqlite3", read_only=True)
    run_id = UUID(metadata["run_id"])
    return await store.get(run_id), await store.list_events(run_id)


async def pause(spawn_worker, *, action="new", label="initial", call=1):
    options = {"gate_call": call}
    if action == "resume":
        # Each invocation is a fresh operating-system process.
        worker = await spawn_worker(action, label=label, **options)
    else:
        worker = await spawn_worker(label=label, **options)
    await worker.boundary("approval")
    worker.process.send_signal(signal.SIGINT)
    assert (await worker.finish())[0] == 130
    return worker


async def successful_finish(worker):
    await worker.boundary("finalization")
    metadata = origin(worker.root)
    repository = Path(metadata["repository_root"])
    assert (repository / "effects.txt").read_text().splitlines() == ["c0", "c1", "c2"]
    assert (
        hashlib.sha256((repository / ".git/index").read_bytes()).hexdigest()
        == metadata["index_sha256"]
    )
    record, events = await stored(worker.root)
    state = record.state
    assert state.lifecycle.status.value == "succeeded"
    usage = state.usage
    assert usage.model_requests == 2 and usage.unconfirmed_model_requests == 0
    assert (usage.input_tokens, usage.output_tokens, usage.cost_usd) == (
        140,
        30,
        Decimal("0.00020"),
    )
    worker.release()
    code, output = await worker.finish()
    assert code == 0, output
    result = json.loads((worker.root / f"result-{worker.label}.json").read_text())[
        "run_result"
    ]
    assert result["reason"] == "task_verified" and result["output"] == "resume-local-ok"
    assert not repository.exists()
    return record, events


async def test_local_fixed_success_after_sigint(spawn_worker, timing):
    first = await pause(spawn_worker)
    before, _ = await stored(first.root)
    assert before.state.lifecycle.status.value == "suspended"
    metadata = origin(first.root)
    assert (Path(metadata["repository_root"]) / "effects.txt").read_text() == "c0\n"
    await asyncio.sleep(0.75)
    resumed = await spawn_worker(
        "resume", label="resumed", run_id=metadata["run_id"], gate_finalization=True
    )
    record, events = await successful_finish(resumed)
    assert str(record.state.identity.run_id) == metadata["run_id"]
    assert record.state.identity.created_at.isoformat() == metadata["created_at"]
    assert sum(e.type.value == "resume_started" for e in events) == 1
    commits = [
        json.loads(line)
        for line in (first.root / "commits.jsonl").read_text().splitlines()
    ]
    suspended = next(c["time"] for c in commits if "run_suspended" in c["types"])
    completed = next(c["time"] for c in commits if "resume_completed" in c["types"])
    finished = next(c["time"] for c in commits if "run_finished" in c["types"])
    active = suspended - metadata["time"] + finished - completed
    pause_seconds = completed - suspended
    assert pause_seconds >= 0.75
    assert (
        abs(record.state.usage.active_duration_ms / 1000 - active)
        <= timing["epsilon_seconds"]
    )
    (first.root / "duration-oracle.json").write_text(
        json.dumps(
            {
                "active_segments": active,
                "excluded_pause": pause_seconds,
                "stored_ms": record.state.usage.active_duration_ms,
                "epsilon": timing["epsilon_seconds"],
            }
        )
    )


async def test_four_resumes_preserve_origin_and_usage(spawn_worker):
    first = await pause(spawn_worker)
    metadata = origin(first.root)
    recovering = await spawn_worker(
        "resume",
        label="recovering",
        run_id=metadata["run_id"],
        fault_event="resume_started",
    )
    barrier = await recovering.boundary("commit")
    record, events = await stored(first.root)
    assert record.last_event_sequence == barrier["sequence"]
    assert record.state.lifecycle.status.value == "recovering"
    assert events[-1].type.value == "resume_started"
    recovering.process.kill()
    assert (await recovering.finish())[0] == -signal.SIGKILL
    for label, call in [("second", 1), ("third", 2)]:
        worker = await spawn_worker(
            "resume", label=label, run_id=metadata["run_id"], gate_call=call
        )
        await worker.boundary("approval")
        worker.process.send_signal(signal.SIGINT)
        assert (await worker.finish())[0] == 130
    final = await spawn_worker(
        "resume", label="fourth", run_id=metadata["run_id"], gate_finalization=True
    )
    record, events = await successful_finish(final)
    assert sum(e.type.value == "resume_started" for e in events) == 4
    assert record.state.identity.created_at.isoformat() == metadata["created_at"]


async def test_unmatched_barrier_cannot_pass_as_interruption(spawn_worker):
    worker = await spawn_worker(gate_call=99)
    with pytest.raises(AssertionError, match="was not reached"):
        await worker.boundary("approval", timeout=10)
    assert (await worker.finish())[0] == 0


async def test_default_signal_deadline_uses_one_precalibrated_budget(
    spawn_worker, timing
):
    worker = await spawn_worker(deadline_probe=True)
    await worker.boundary("deadline")
    from time import monotonic

    started = monotonic()
    worker.process.send_signal(signal.SIGINT)
    cleanup = await worker.boundary("cleanup")
    diagnostic = await worker.boundary("diagnostic")
    assert cleanup["deadline"] == diagnostic["deadline"]
    assert (await worker.finish(timeout=timing["supervision_seconds"]))[0] == 130
    elapsed = monotonic() - started
    (worker.root / "deadline-measurement.json").write_text(
        json.dumps({"elapsed": elapsed, "calibration": timing}, indent=2)
    )
    assert 29 <= elapsed <= 30 + timing["epsilon_seconds"]


async def test_signal_while_real_console_stdin_is_open(spawn_worker):
    worker = await spawn_worker(console_approval=True)
    assert worker.process.stdout is not None
    async with asyncio.timeout(10):
        await worker.process.stdout.readuntil(b"[y/N]: ")
    worker.process.send_signal(signal.SIGINT)
    # Leaving stdin open proves that no abandoned input thread delays asyncio.run.
    async with asyncio.timeout(3):
        assert await worker.process.wait() == 130
    assert (await stored(worker.root))[0].state.lifecycle.status.value == "suspended"
