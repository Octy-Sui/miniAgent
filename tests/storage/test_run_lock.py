import os
import signal
import subprocess
import sys
from pathlib import Path
from select import select
from time import monotonic
from uuid import UUID, uuid4

import pytest

from miniagent.storage.run_lock import RunInUseError, RunLock

WORKER = """
import subprocess, sys
from pathlib import Path
from select import select
from time import monotonic
from uuid import UUID
from miniagent.storage.run_lock import RunInUseError, RunLock
try:
    with RunLock(Path(sys.argv[1]), UUID(sys.argv[2])):
        print("held", monotonic(), flush=True)
        if sys.argv[3] == "hold":
            sys.stdin.readline()
        elif sys.argv[3] == "child":
            subprocess.Popen([sys.executable, "-c", "import os, signal; print('child', os.getpid(), flush=True); signal.pause()"], close_fds=False)
            sys.stdin.readline()
except RunInUseError:
    print("RUN_IN_USE", monotonic(), flush=True)
    sys.exit(1)
"""


def worker(database: Path, run_id: UUID, mode: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", WORKER, str(database), str(run_id), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def read_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    deadline = monotonic() + 5.0
    data = bytearray()
    while True:
        ready, _, _ = select([process.stdout], [], [], max(0, deadline - monotonic()))
        assert ready, "worker handshake timed out"
        chunk = os.read(process.stdout.fileno(), 1)
        assert chunk, "worker exited before handshake"
        if chunk == b"\n":
            return data.decode()
        data.extend(chunk)


def test_same_run_lock_conflicts_until_release(tmp_path: Path) -> None:
    """TC-U20-04/05: loser finishes strictly inside the winner's lock interval."""
    database = tmp_path / "runs.sqlite3"
    run_id = uuid4()
    winner = worker(database, run_id, "hold")
    try:
        line = read_line(winner).split()
        assert line[0] == "held"
        acquired = float(line[1])
        attempted = monotonic()
        loser = worker(database, run_id, "try")
        output, error = loser.communicate(timeout=5)
        result = monotonic()
        assert loser.returncode == 1 and "RUN_IN_USE" in output, error
        alias = tmp_path / "unused" / ".." / "runs.sqlite3"
        with pytest.raises(RunInUseError) as caught, RunLock(alias, run_id):
            pytest.fail("conflicting alias acquired")
        assert caught.value.run_id == run_id
        assert winner.poll() is None
        winner.communicate("release\n", timeout=5)
        released = monotonic()
        assert acquired < attempted < result < released
        with RunLock(database, run_id):
            pass
    finally:
        if winner.poll() is None:
            winner.kill()
        winner.wait(timeout=5)


def test_lock_survives_file_reuse_and_is_not_inherited(tmp_path: Path) -> None:
    """TC-U20-06/07: killed holder releases while its exec child remains alive."""
    database = tmp_path / "runs.sqlite3"
    run_id = uuid4()
    winner = worker(database, run_id, "child")
    child_pid = None
    try:
        assert read_line(winner).startswith("held ")
        child_line = read_line(winner).split()
        assert child_line[0] == "child"
        child_pid = int(child_line[1])
        lock_file = tmp_path / ".miniagent-run-locks" / f"{run_id.hex}.lock"
        inode_before = lock_file.stat().st_ino
        winner.kill()
        winner.wait(timeout=5)
        os.kill(child_pid, 0)
        successor = worker(database, run_id, "try")
        output, error = successor.communicate(timeout=5)
        assert successor.returncode == 0 and output.startswith("held "), error
        os.kill(child_pid, 0)
        assert lock_file.stat().st_ino == inode_before
    finally:
        if winner.poll() is None:
            winner.kill()
        winner.wait(timeout=5)
        if child_pid is not None:
            os.kill(child_pid, signal.SIGTERM)
        for stream in (winner.stdout, winner.stderr, winner.stdin):
            if stream is not None:
                stream.close()


def test_distinct_runs_can_hold_locks(tmp_path: Path) -> None:
    with (
        RunLock(tmp_path / "runs.sqlite3", uuid4()),
        RunLock(tmp_path / "runs.sqlite3", uuid4()),
    ):
        pass
