import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def model_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {
                    "id": "request",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "done"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.mark.parametrize("entry", ["direct", "run", "trigger"])
@pytest.mark.parametrize(
    ("flag", "setting", "expected"),
    [
        (None, None, False),
        (None, True, True),
        (False, True, False),
        (True, False, True),
        (False, False, False),
        (True, True, True),
    ],
)
def test_option_precedence_through_real_entrypoints(
    configured_host, monkeypatch, model_endpoint, entry, flag, setting, expected
):
    from tests.support.cli.bootstrap import create_source_repository

    root = Path.cwd()
    source = create_source_repository(configured_host / "source")
    environment = dict(
        os.environ,
        OPENAI_BASE_URL=model_endpoint,
        PYTHONPATH=str(root / "src"),
        MODEL_LIMITS_FILE=str(root / "config/model_limits.toml"),
        MODEL_PRICING_FILE=str(root / "config/model_pricing.toml"),
    )
    environment.pop("EXPORT_TRAJECTORY", None)
    if setting is not None:
        environment["EXPORT_TRAJECTORY"] = str(setting).lower()
    if entry == "trigger":
        scenario_path = configured_host / "docker-scenario.json"
        scenario = json.loads(scenario_path.read_text())
        scenario["container_id"] = "c" * 64
        scenario_path.write_text(json.dumps(scenario))
        command = [
            sys.executable,
            str(root / "trigger"),
            "--instance-id",
            "django__django-11477",
            "--model",
            "deepseek-v4-pro-0813",
        ]
    else:
        command = (
            [sys.executable, "-m", "miniagent.cli.main"]
            + (["run"] if entry == "run" else [])
            + ["task"]
        )
    if flag is not None:
        command.append("--trajectory" if flag else "--no-trajectory")
    result = subprocess.run(
        command,
        cwd=source,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    import sqlite3

    with sqlite3.connect(configured_host / "journal.sqlite3") as connection:
        context = json.loads(
            connection.execute("SELECT recovery_context_json FROM runs").fetchone()[0]
        )
        state = json.loads(
            connection.execute("SELECT state_json FROM runs").fetchone()[0]
        )
    assert context["application"]["payload"]["export_trajectory"] is expected
    assert ("Trajectory: " in result.stdout) is expected
    assert "TRAJECTORY_EXPORT_FAILED" not in result.stderr
    if expected:
        from urllib.parse import urlparse

        manifests = [
            json.loads(path.read_text())
            for path in (configured_host / "artifacts/manifests").rglob("*.json")
        ]
        trajectory = next(
            item for item in manifests if item["type"] == "run_trajectory"
        )
        document = json.loads(Path(urlparse(trajectory["uri"]).path).read_bytes())
        projected, frozen = document["definition"], context["application"]["payload"]
        assert projected["task"] == state["identity"]["task"]
        assert projected["agent"] == state["definition"]["agent_spec"]
        assert projected["tools"] == state["definition"]["tool_definitions"]
        assert projected["budgets"] == state["definition"]["config"]
        assert projected["model_limits"] == frozen["model_limits"]
        assert projected["pricing"] == frozen["pricing"]
        assert (
            projected["provider_endpoint_identity"]
            == frozen["provider"]["endpoint_identity"]
        )
        assert projected["workspace_kind"] == (
            "docker" if entry == "trigger" else "local"
        )
        assert projected["base_revision"] == context["attachment"]["base_revision"]
        assert projected["evaluator"] == frozen["evaluator"]["kind"]
        if entry == "trigger":
            evaluator = frozen["evaluator"]
            assert projected["benchmark"] == {
                "instance_id": "django__django-11477",
                "task_digest": evaluator["record_sha256"],
                "manifest_sha256": evaluator["manifest_sha256"],
                "tasks_sha256": evaluator["tasks_sha256"],
                "harness_version": "5.0.2",
            }
        else:
            assert projected["benchmark"] is None


@pytest.mark.parametrize("frozen", [False, True])
async def test_resume_ignores_reversed_environment_and_dry_run_writes_nothing(
    resume_case, monkeypatch, frozen
):
    import sqlite3

    from miniagent.cli.application import ApplicationResult
    from miniagent.cli.resume import ResumeConfig, resume_application
    from miniagent.cli.run_definition import (
        freeze_application_definition,
        parse_application_definition,
    )
    from miniagent.runtime import CancellationToken

    app, _execution, provider = resume_case
    deps = app.dependencies
    record = await deps.run_store.get(deps.run_id)
    definition = parse_application_definition(
        record.recovery_context.application, record.state.definition
    ).model_copy(update={"export_trajectory": frozen})
    context = record.recovery_context.model_copy(
        update={
            "application": freeze_application_definition(
                definition, record.state.definition
            )
        }
    )
    with sqlite3.connect(deps.run_store.path) as connection:
        connection.execute(
            "UPDATE runs SET recovery_context_json=? WHERE run_id=?",
            (context.model_dump_json(), str(deps.run_id)),
        )
    before = await deps.run_store.get(deps.run_id)
    before_manifests = tuple((deps.artifact_store.root / "manifests").rglob("*.json"))
    monkeypatch.setenv("EXPORT_TRAJECTORY", str(not frozen).lower())
    await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path, dry_run=True),
        cancellation_token=CancellationToken(),
    )
    assert await deps.run_store.get(deps.run_id) == before
    assert (
        tuple((deps.artifact_store.root / "manifests").rglob("*.json"))
        == before_manifests
    )
    assert provider.requests == []
    result = await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
        cancellation_token=CancellationToken(),
    )
    assert isinstance(result, ApplicationResult)
    assert result.trajectory_export.status == ("exported" if frozen else "disabled")
    assert len(provider.requests) == 1


@pytest.mark.parametrize("flag", ["--trajectory", "--no-trajectory"])
def test_resume_rejects_export_switch(flag):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "miniagent.cli.main",
            "resume",
            "--run-id",
            "00000000-0000-0000-0000-000000000001",
            flag,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 2
    assert "No such option" in result.stderr
