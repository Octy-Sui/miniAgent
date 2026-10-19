import asyncio
import sys
from uuid import UUID

import pytest
from typer.testing import CliRunner

from miniagent.cli.main import parse_run_id, resume_app, run_app


@pytest.mark.parametrize(
    ("value",),
    [
        ("12345678-1234-4234-8234-123456789abc",),
        ("12345678123442348234123456789abc",),
        ("run_12345678-1234-4234-8234-123456789abc",),
        ("run_12345678123442348234123456789abc",),
        ("{12345678-1234-4234-8234-123456789abc}",),
        ("urn:uuid:12345678-1234-4234-8234-123456789abc",),
    ],
)
def test_complete_uuid_forms_identify_same_run(value):
    assert parse_run_id(value) == UUID("12345678-1234-4234-8234-123456789abc")


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


@pytest.mark.parametrize(("route",), [((),), (("run",),), (("resume",),)])
async def test_real_entrypoint_help_has_independent_route(route):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "miniagent.cli.main",
        *route,
        "--help",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr.decode()
    assert (b"--run-id" in stdout) == (route == ("resume",))
    assert (b"--model" in stdout) == (route != ("resume",))


def test_explicit_run_keeps_resume_as_task(monkeypatch):
    from miniagent.cli import main as module

    seen = []

    async def main(task, *args, **kwargs):
        seen.append(task)

    monkeypatch.setattr(module, "main", main)
    assert CliRunner().invoke(run_app, ["resume"]).exit_code == 0
    assert seen == ["resume"]
