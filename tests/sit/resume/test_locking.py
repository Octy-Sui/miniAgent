import asyncio
import json
import sys
from time import monotonic

from tests.support.resume.local import origin, pause


async def loser_inside(worker):
    ready = monotonic()
    metadata = origin(worker.root)
    attempt = monotonic()
    loser = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "miniagent.cli.main",
        "resume",
        "--run-id",
        metadata["run_id"],
        "--database",
        str(worker.root / "runs.sqlite3"),
        "--dry-run",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(loser.communicate(), 10)
    result = monotonic()
    assert loser.returncode == 1 and b"RUN_IN_USE" in stdout + stderr
    permission = monotonic()
    assert ready < attempt < result < permission
    (worker.root / f"lock-{worker.label}.json").write_text(
        json.dumps(
            {
                "winner_ready": ready,
                "loser_attempt": attempt,
                "loser_result": result,
                "release_permission": permission,
                "loser_output": (stdout + stderr).decode(),
            },
            indent=2,
        )
    )
    worker.release()


async def test_loser_attempt_is_inside_winner_lock_interval(spawn_worker):
    winner = await spawn_worker(gate_call=1)
    await winner.boundary("approval")
    await loser_inside(winner)
    assert (await winner.finish())[0] == 0


async def test_lock_covers_finalization(spawn_worker):
    first = await pause(spawn_worker)
    winner = await spawn_worker(
        "resume",
        label="finalizer",
        run_id=origin(first.root)["run_id"],
        gate_finalization=True,
    )
    await winner.boundary("finalization")
    await loser_inside(winner)
    assert (await winner.finish())[0] == 0
