import asyncio
import sys
from uuid import uuid4

from miniagent.environments.process import (
    identify_host_process,
    register_host_process,
)
from tests.support.environments import local as test_local

source_repository = test_local.source_repository


async def held_process():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal; print('ready', flush=True); signal.pause()",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert await asyncio.wait_for(process.stdout.readline(), 5) == b"ready\n"
    return process


def identify(process):
    identity = identify_host_process(
        process.pid, attempt_id=uuid4(), execution_id=uuid4(), role="local_command"
    )
    register_host_process(process, identity)
    return identity
