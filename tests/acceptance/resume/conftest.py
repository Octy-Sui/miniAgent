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

from .environment import calibrate_timing, record_docker_environment, record_environment


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


@pytest.fixture
async def docker_workspace(tmp_path):
    from uuid import uuid4

    from miniagent.artifacts import FileArtifactStore
    from miniagent.environments.config import DockerOptions, DockerWorkspaceSpec
    from miniagent.environments.docker import DockerWorkspaceBackend
    from miniagent.environments.docker_connection import resolve_docker_connection

    image = os.environ.get("MINIAGENT_RESUME_DOCKER_IMAGE")
    if not image:
        pytest.skip(
            "DEFER: explicit prepared MINIAGENT_RESUME_DOCKER_IMAGE is required; no pull"
        )
    connection = await resolve_docker_connection(Path("docker"), dict(os.environ))
    await record_docker_environment(
        tmp_path / "docker-environment.json", connection, image
    )
    # This transparent CLI audit proxy records real daemon commands, without simulating responses.
    executable = tmp_path / "docker-audit"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, subprocess, sys
from pathlib import Path
with (Path(__file__).parent / "docker-cli.jsonl").open("a") as stream:
    stream.write(json.dumps(sys.argv[1:])+"\\n")
raise SystemExit(subprocess.call(["docker", *sys.argv[1:]]))
"""
    )
    executable.chmod(0o755)
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        connection=connection,
        docker_executable=str(executable),
    )
    spec = DockerWorkspaceSpec(
        run_id=uuid4(),
        options=DockerOptions(image=image, workdir="/testbed"),
    )
    workspace = await backend.create(spec)
    attachment = workspace.describe_attachment()
    (tmp_path / "docker-attachment.json").write_text(
        attachment.model_dump_json(indent=2)
    )
    (tmp_path / "docker-spec.json").write_text(spec.model_dump_json(indent=2))
    try:
        yield backend, workspace, spec, connection
    finally:
        # Never remove by name or discover unrelated resources.
        process = await asyncio.create_subprocess_exec(
            "docker",
            "--host",
            connection.endpoint,
            "inspect",
            "--format",
            '{{index .Config.Labels "miniagent.run_id"}}',
            workspace.container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            assert stdout.decode().strip() == str(spec.run_id)
            await workspace.destroy()
