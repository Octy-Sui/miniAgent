import json
import os
import subprocess
import sys

import pytest

from tests.support.cli.agent import DEFAULT_AGENT


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (
            ["-m", "miniagent.cli.main", "task", "--model", "missing-model"],
            "missing model limits",
        ),
        (
            ["trigger", "--instance-id", "django__django-11477"],
            "EVALUATION_DATA_INVALID",
        ),
        (
            ["eval.py", "--instance-id", "astropy__astropy-14539"],
            "EVALUATION_DATA_INVALID",
        ),
    ],
)
def test_entrypoint_preparation_failure_has_live_and_persisted_diagnostics(
    tmp_path, arguments, error
):
    limits = tmp_path / "limits.toml"
    limits.write_text("models = []\n")
    result = subprocess.run(
        [sys.executable, *arguments],
        env={
            **os.environ,
            "OPENAI_API_KEY": "secret-test-key",
            "OPENAI_BASE_URL": "https://example.test",
            "WORKSPACE_BACKEND": "local",
            "MODEL_LIMITS_FILE": str(limits),
            "SWEBENCH_DATA_ROOT": str(tmp_path / "absent"),
            "RUN_DATABASE_PATH": str(tmp_path / "runs.sqlite3"),
            "MINIAGENT_LOG_DIR": str(tmp_path / "logs"),
        },
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "invocation.started" in result.stderr
    assert error in result.stdout + result.stderr
    if arguments[0] == "eval.py":
        assert "batch.worker_started" in result.stderr
        assert "batch.worker_completed" in result.stderr
    else:
        assert "workspace_prepare.started" in result.stderr
    records = [
        json.loads(line)
        for path in (tmp_path / "logs").rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(record["event"] == "workspace_prepare.failed" for record in records)
    assert "secret-test-key" not in json.dumps(records)


def test_trigger_default_matches_configured_default_agent():
    result = subprocess.run(
        [sys.executable, "trigger", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "160"},
    )
    assert result.returncode == 0
    assert DEFAULT_AGENT.model.model in result.stdout


def test_resume_missing_run_keeps_rejection_and_logs_inspection(tmp_path):
    run_id = "00000000-0000-0000-0000-000000000001"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "miniagent.cli.main",
            "resume",
            "--run-id",
            run_id,
            "--database",
            str(tmp_path / "missing.sqlite3"),
            "--dry-run",
        ],
        env={**os.environ, "MINIAGENT_LOG_DIR": str(tmp_path / "logs")},
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert "RUN_NOT_FOUND" in result.stderr
    assert "resume_inspect.started" in result.stderr
    assert run_id in result.stderr
    assert not (tmp_path / "missing.sqlite3").exists()
