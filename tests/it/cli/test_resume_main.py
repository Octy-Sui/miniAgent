from uuid import UUID

import pytest
from typer.testing import CliRunner

from miniagent.cli.main import resume_app, run_app


@pytest.mark.parametrize(
    ("value",),
    [("12345678",), ("run_12345678",), ("run_xxx",), ("not-a-uuid",)],
)
def test_short_or_arbitrary_ids_are_parameter_errors(value):
    assert CliRunner().invoke(resume_app, ["--run-id", value]).exit_code == 2


@pytest.mark.parametrize(
    ("option",),
    [
        ("--model",),
        ("--task",),
        ("--workspace",),
        ("--docker-image",),
        ("--force",),
        ("--checkpoint",),
    ],
)
def test_resume_refuses_frozen_overrides(option):
    result = CliRunner().invoke(
        resume_app, ["--run-id", str(UUID(int=1)), option, "override"]
    )
    assert result.exit_code == 2


def test_explicit_run_keeps_resume_as_task(monkeypatch):
    from miniagent.cli import main as module

    seen = []

    async def main(task, *args, **kwargs):
        seen.append(task)

    monkeypatch.setattr(module, "main", main)
    assert CliRunner().invoke(run_app, ["resume"]).exit_code == 0
    assert seen == ["resume"]
