import asyncio
import json
import os
import sys
from dataclasses import dataclass
from multiprocessing import Pipe
from multiprocessing.connection import Connection
from pathlib import Path
from time import monotonic

import pytest

from .environment import calibrate_timing, record_environment


async def receive(channel):
    loop = asyncio.get_running_loop()
    result = loop.create_future()

    def readable():
        if result.done():
            return
        try:
            result.set_result(channel.recv())
        except EOFError as exc:
            result.set_exception(exc)

    loop.add_reader(channel.fileno(), readable)
    try:
        return await result
    finally:
        loop.remove_reader(channel.fileno())


@dataclass
class Worker:
    process: asyncio.subprocess.Process
    channel: Connection
    root: Path
    label: str
    observations: list

    async def boundary(self, expected: str, *, timeout: float = 40):
        try:
            async with asyncio.timeout(timeout):
                message = await receive(self.channel)
        except (EOFError, TimeoutError) as exc:
            details = ""
            if isinstance(exc, EOFError):
                _, details = await self.finish()
            raise AssertionError(
                f"Expected barrier {expected!r} was not reached: {details}"
            ) from exc
        self.observations.append(
            {"supervisor_received": monotonic(), "message": message}
        )
        (self.root / f"barriers-{self.label}.json").write_text(
            json.dumps(self.observations, indent=2)
        )
        assert message.get("boundary") == expected, message
        return message

    def release(self):
        self.channel.send("continue")

    async def finish(self, *, timeout=10):
        stdout, stderr = await asyncio.wait_for(self.process.communicate(), timeout)
        (self.root / f"stdout-{self.label}.txt").write_bytes(stdout)
        (self.root / f"stderr-{self.label}.txt").write_bytes(stderr)
        return self.process.returncode, (stdout + stderr).decode()


@pytest.fixture
async def spawn_worker(tmp_path):
    record_environment(tmp_path / "environment.json")
    workers = []

    async def spawn(action="new", *, label="initial", **options):
        parent, child = Pipe()
        arguments = [
            sys.executable,
            "-m",
            "tests.acceptance.resume.worker",
            action,
            str(tmp_path),
            "--channel-fd",
            str(child.fileno()),
            "--label",
            label,
        ]
        for key, value in options.items():
            if value is True:
                arguments.append("--" + key.replace("_", "-"))
            elif value is not False and value is not None:
                arguments.extend(["--" + key.replace("_", "-"), str(value)])
        process = await asyncio.create_subprocess_exec(
            *arguments,
            pass_fds=(child.fileno(),),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ),
        )
        child.close()
        worker = Worker(process, parent, tmp_path, label, [])
        workers.append(worker)
        return worker

    yield spawn
    for worker in workers:
        if worker.process.returncode is None:
            worker.process.kill()
            await worker.process.wait()
        worker.channel.close()


@pytest.fixture
async def timing(tmp_path):
    return await calibrate_timing(tmp_path / "timing-calibration.json")
