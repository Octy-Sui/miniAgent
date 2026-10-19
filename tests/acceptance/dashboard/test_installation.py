import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]


def command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass(frozen=True)
class Installations:
    root: Path
    wheel: Path
    minimal: Path
    dashboard: Path


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Installations:
    root = tmp_path_factory.mktemp("dashboard-wheel")
    result = command(["uv", "build", "--wheel", "--out-dir", str(root)], cwd=REPOSITORY)
    assert result.returncode == 0, result.stderr
    (wheel,) = root.glob("*.whl")
    environments = []
    for name, extra in [("minimal", ""), ("dashboard", "[dashboard]")]:
        environment = root / name
        result = command(
            ["uv", "venv", "--python", sys.executable, str(environment)], cwd=root
        )
        assert result.returncode == 0, result.stderr
        result = command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(environment / "bin/python"),
                str(wheel) + extra,
            ],
            cwd=root,
        )
        assert result.returncode == 0, result.stderr
        environments.append(environment)
    return Installations(root, wheel, *environments)


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

    from tests.dashboard.builders import make_database, make_run

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


def test_wheel_preserves_existing_task_entry(
    installed: Installations, tmp_path: Path
) -> None:
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(
                (
                    self.path,
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                )
            )
            body = json.dumps(
                {
                    "id": "local-fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "qwen3.8-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "wheel-task-answer",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "source.txt").write_text("wheel compatibility fixture")
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "add", "source.txt"],
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        assert command(args, cwd=task_dir).returncode == 0
    env = {
        **os.environ,
        "OPENAI_API_KEY": "local-fake",
        "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "MODEL_LIMITS_FILE": str(REPOSITORY / "config/model_limits.toml"),
        "MODEL_PRICING_FILE": str(REPOSITORY / "config/model_pricing.toml"),
        "RUN_DATABASE_PATH": str(tmp_path / "task.sqlite3"),
        "LOCAL_WORKSPACE_ROOT": str(tmp_path / "workspaces"),
        "ARTIFACT_ROOT": str(tmp_path / "artifacts"),
        "WORKSPACE_BACKEND": "local",
    }
    try:
        result = command(
            [
                str(installed.minimal / "bin/miniagent"),
                "answer from this fixture",
                "--model",
                "qwen3.8-flash",
            ],
            cwd=task_dir,
            env=env,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "wheel-task-answer" in result.stdout
        assert len(requests) == 1
        assert requests[0][0] == "/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
