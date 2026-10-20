import asyncio
import sys

import pytest


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
