import hashlib
import json
import signal
from decimal import Decimal
from pathlib import Path
from uuid import UUID

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
