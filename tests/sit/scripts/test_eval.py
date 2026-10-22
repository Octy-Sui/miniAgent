import asyncio
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from miniagent.cli.eval_batch import TaskManifest, app, execute_batch, select_tasks


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["all", "--instance-id", "astropy__astropy-14539"],
        ["--instance-id", "astropy__astropy-12907"],
        ["--instance-id", "missing"],
        ["all", "--concurrency", "4"],
        ["all", "--concurrency", "0"],
    ],
)
def test_invalid_selection_is_rejected_before_execution(arguments):
    assert CliRunner().invoke(app, arguments).exit_code == 2


async def test_batch_writes_summary_for_each_real_worker_preparation_failure(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("SWEBENCH_DATA_ROOT", str(tmp_path / "missing-data"))
    monkeypatch.setenv("RUN_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test")
    manifest = TaskManifest.model_validate_json(
        Path("docs/swebench-task-base.json").read_text()
    )
    tasks = select_tasks(
        manifest.tasks, None, ["astropy__astropy-14539", "django__django-16485"]
    )
    summary = await execute_batch(tasks, "qwen3.8-flash", tmp_path, 2)
    saved = json.loads((tmp_path / "summary.json").read_text())
    assert summary.pass_rate == 0
    assert saved["total"] == 2
    assert saved["unknown_cost_tasks"] == 2
    assert [task["instance_id"] for task in saved["tasks"]] == [
        "astropy__astropy-14539",
        "django__django-16485",
    ]
    for task in saved["tasks"]:
        assert task["status"] == "error"
        assert "EVALUATION_DATA_INVALID" in task["error"]
        assert (tmp_path / task["log"]).is_file()
        assert (tmp_path / f"{task['instance_id']}.result.json").is_file()


async def test_sigterm_stops_worker_and_runs_parent_cleanup(tmp_path: Path):
    # A process boundary fake exercises the parent's real signal handling.
    script = tmp_path / "parent.py"
    script.write_text("""import asyncio, sys
from pathlib import Path
from miniagent.cli.eval_batch import run_jobs
root = Path(sys.argv[1])
async def main():
    try:
        await run_jobs([[sys.executable, str(root / "worker.py"), str(root)]], root, 1)
    finally:
        (root / "parent-cleaned").touch()
asyncio.run(main())
""")
    (tmp_path / "worker.py").write_text("""import signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
def stop(*args):
    (root / "worker-cleaned").touch()
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
(root / "ready").touch()
time.sleep(30)
""")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            while not (tmp_path / "ready").exists():
                await asyncio.sleep(0.01)
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 5)
        assert (tmp_path / "worker-cleaned").exists()
        assert (tmp_path / "parent-cleaned").exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
