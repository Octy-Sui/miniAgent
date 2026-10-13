import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def trigger_environment(tmp_path: Path) -> dict[str, str]:
    executable = tmp_path / "miniagent"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n"
        "sys.exit(17)\n"
    )
    executable.chmod(0o755)
    return {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}


@pytest.mark.parametrize(
    ("instance_id",),
    [
        ("astropy__astropy-12907",),
        ("django__django-11477",),
        ("pytest-dev__pytest-7571",),
        ("sympy__sympy-13647",),
        ("pydata__xarray-3677",),
    ],
)
def test_trigger_launches_selected_development_task(
    instance_id: str, trigger_environment: dict[str, str]
) -> None:
    manifest = json.loads(Path("docs/swebench-task-base.json").read_text())
    task = next(
        task for task in manifest["tasks"] if task["instance_id"] == instance_id
    )

    result = subprocess.run(
        [sys.executable, "trigger", "--instance-id", instance_id],
        env=trigger_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 17, result.stderr
    arguments = json.loads(result.stdout)
    assert arguments == [
        (
            "Reproduce the problem, implement the minimal fix, and run the relevant tests."
            f"\n\n{task['problem_statement']}"
        ),
        "--workspace",
        "docker",
        "--docker-image",
        task["image"],
        "--model",
        "deepseek-v4-pro",
    ]
    if task["hints_text"]:
        assert task["hints_text"] not in arguments[0]


def test_trigger_overrides_default_model(trigger_environment: dict[str, str]) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "trigger",
            "--instance-id",
            "astropy__astropy-12907",
            "--model",
            "another-model",
        ],
        env=trigger_environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 17, result.stderr
    assert json.loads(result.stdout)[-2:] == ["--model", "another-model"]


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
