import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def trigger_environment(
    tmp_path: Path,
    prepared_bundle: Path,
    docker_cli: Path,
    harness_executable: Path,
) -> dict[str, str]:
    (tmp_path / "docker").symlink_to(docker_cli)
    limits = tmp_path / "limits.toml"
    limits.write_text("models = []\n")
    return {
        **os.environ,
        "OPENAI_API_KEY": "test",
        "OPENAI_BASE_URL": "https://example.test",
        "SWEBENCH_DATA_ROOT": str(prepared_bundle),
        "SWEBENCH_WORK_ROOT": str(tmp_path / "runs"),
        "SWEBENCH_EXECUTABLE": str(harness_executable),
        "EVALUATION_DOCKER_EXECUTABLE": str(docker_cli),
        "MODEL_LIMITS_FILE": str(limits),
        "RUN_DATABASE_PATH": str(tmp_path / "journal.sqlite3"),
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
    }


def test_trigger_passes_task_context_to_real_bootstrap(
    tmp_path: Path, prepared_bundle: Path
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "trigger",
            "--instance-id",
            "pydata__xarray-3677",
            "--model",
            "deepseek-v4-pro-0813",
        ],
        env={
            **os.environ,
            "OPENAI_API_KEY": "test",
            "OPENAI_BASE_URL": "https://example.test",
            "SWEBENCH_DATA_ROOT": str(prepared_bundle),
            "RUN_DATABASE_PATH": str(tmp_path / "journal.sqlite3"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "TASK_NOT_READY: pydata__xarray-3677" in result.stderr
    assert not (tmp_path / "journal.sqlite3").exists()


@pytest.mark.parametrize(
    ("instance_id", "error"),
    [
        ("astropy__astropy-12907", "TASK_NOT_READY: astropy__astropy-12907"),
        ("django__django-11477", "missing model limits: qwen/qwen3.8-flash"),
        ("pytest-dev__pytest-7571", "TASK_NOT_READY: pytest-dev__pytest-7571"),
        ("sympy__sympy-13647", "TASK_NOT_READY: sympy__sympy-13647"),
        ("pydata__xarray-3677", "TASK_NOT_READY: pydata__xarray-3677"),
    ],
)
def test_trigger_prepares_selected_development_task(
    instance_id: str, error: str, trigger_environment: dict[str, str]
) -> None:
    result = subprocess.run(
        [sys.executable, "trigger", "--instance-id", instance_id],
        env=trigger_environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert error in result.stderr


def test_trigger_overrides_default_model(trigger_environment: dict[str, str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "trigger",
            "--instance-id",
            "django__django-11477",
            "--model",
            "another-model",
        ],
        env=trigger_environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    assert "missing model limits: qwen/another-model" in result.stderr


@pytest.mark.parametrize(
    ("instance_id", "error"),
    [
        ("missing-task", "Unknown instance ID"),
        ("django__django-16485", "development tasks only"),
    ],
)
def test_trigger_rejects_invalid_selection_before_launching(
    instance_id: str, error: str, trigger_environment: dict[str, str]
) -> None:
    result = subprocess.run(
        [sys.executable, "trigger", "--instance-id", instance_id],
        env=trigger_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert error in result.stderr
    assert result.stdout == ""
