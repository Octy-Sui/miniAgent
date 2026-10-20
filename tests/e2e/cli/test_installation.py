import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.support.dashboard.installation import REPOSITORY, Installations, command


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
