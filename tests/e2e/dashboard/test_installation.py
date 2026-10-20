import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from tests.support.dashboard.installation import Installations, command


def test_missing_extra_explains_installation(installed: Installations) -> None:
    script = installed.minimal / "bin/miniagent-dashboard"
    assert script.exists()
    result = command(
        [str(script), "--database", str(installed.root / "absent.sqlite3")],
        cwd=installed.root,
    )
    assert result.returncode != 0
    assert "uv sync --extra dashboard" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    result = command(
        [
            str(installed.minimal / "bin/python"),
            "-c",
            'import importlib.util; assert importlib.util.find_spec("fastapi") is None; import miniagent.cli.main; from miniagent.dashboard.cli import app',
        ],
        cwd=installed.root,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("hostile_env", [False, True])
def test_wheel_serves_explicit_database_without_provider_settings(
    installed: Installations, tmp_path: Path, hostile_env: bool
) -> None:
    import asyncio

    from tests.support.dashboard.builders import make_database, make_run

    database = asyncio.run(make_database(tmp_path / "explicit.sqlite3"))
    run_id = asyncio.run(make_run(database, 12345, task="explicit-wheel-database"))
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("OPENAI_", "QWEN_", "MINIAGENT_")) or key in {
            "RUN_DATABASE_PATH",
            "ARTIFACT_ROOT",
        }:
            env.pop(key)
    if hostile_env:
        (tmp_path / ".env").write_text(
            "OPENAI_BASE_URL=not-a-url\nWORKSPACE_BACKEND=invalid\nRUN_DATABASE_PATH=other.sqlite3\n"
        )
        env["RUN_DATABASE_PATH"] = str(tmp_path / "other.sqlite3")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    artifacts = tmp_path / "missing-artifacts"
    process = subprocess.Popen(
        [
            str(installed.dashboard / "bin/miniagent-dashboard"),
            "--database",
            str(database),
            "--artifacts",
            str(artifacts),
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz", timeout=0.5
                ) as response:
                    assert response.status == 200
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("installed dashboard failed to start")
                time.sleep(0.05)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/runs", timeout=3
        ) as response:
            page = response.read().decode()
            assert str(run_id) in page
            assert "explicit-wheel-database" in page
        for resource in (
            "dashboard.css",
            "dashboard.js",
            "vendor/htmx.min.js",
            "vendor/htmx.LICENSE",
        ):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/static/{resource}", timeout=3
            ) as response:
                assert response.status == 200
                assert response.read()
        assert not artifacts.exists()
        assert not (tmp_path / "other.sqlite3").exists()
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    assert "GET /runs" not in stdout.decode() + stderr.decode()
    with zipfile.ZipFile(installed.wheel) as wheel:
        assert "miniagent/dashboard/templates/run.html" in wheel.namelist()
        assert "miniagent/dashboard/static/vendor/htmx.LICENSE" in wheel.namelist()
