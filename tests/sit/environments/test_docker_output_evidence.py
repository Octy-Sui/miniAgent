import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactPersistenceError, FileArtifactStore
from miniagent.environments import (
    Command,
    DockerOptions,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
)
from miniagent.environments.managed_execution import (
    MemoryExecutionObserver,
    execution_scope,
)


@pytest.fixture
async def docker_output_case(tmp_path: Path):
    image = os.environ.get("MINIAGENT_DOCKER_IMAGE")
    if image is None:
        images = (
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "image", "ls", "--format", "{{.Repository}} {{.ID}}"],
                check=True,
                capture_output=True,
                text=True,
            )
        ).stdout
        assert isinstance(images, str)
        image = next(
            (
                line.split()[1]
                for line in images.splitlines()
                if line.startswith("swebench/")
            ),
            None,
        )
    assert image is not None, (
        "NOT-RUN: no existing local task image; never pull implicitly"
    )
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    backend = DockerWorkspaceBackend(artifact_store=artifacts, max_output_bytes=1024)
    workspace = await backend.create(
        DockerWorkspaceSpec(
            run_id=uuid4(), options=DockerOptions(image=image, workdir="/testbed")
        )
    )
    try:
        yield workspace, artifacts
    finally:
        await workspace.destroy()


@pytest.mark.parametrize("variant", ["binary", "utf8", "empty"])
async def test_real_docker_complete_streams(docker_output_case, variant):
    workspace, artifacts = docker_output_case
    expression = (
        "bytes(range(256))*4096"
        if variant == "binary"
        else "('汉字🙂'*100000).encode()"
        if variant == "utf8"
        else "b''"
    )
    expected = (
        bytes(range(256)) * 4096
        if variant == "binary"
        else ("汉字🙂" * 100000).encode()
        if variant == "utf8"
        else b""
    )
    observer = MemoryExecutionObserver()
    with execution_scope(observer):
        result = await workspace.execute(
            Command(
                argv=[
                    "python3",
                    "-c",
                    f"import sys;sys.stdout.buffer.write({expression});sys.stderr.buffer.write(b'stderr')",
                ],
                timeout_seconds=30,
            )
        )
    output = result.output_evidence
    assert output is not None
    assert output.stdout.ref is not None and output.stderr.ref is not None
    assert await artifacts.read(output.stdout.ref) == expected
    assert await artifacts.read(output.stderr.ref) == b"stderr"
    assert output.stdout.eof_observed and output.stderr.eof_observed
    assert output.stdout.status == output.stderr.status == "complete"
    assert result.stdout_truncated == (len(expected) > 1024)
    assert observer.commands[output.execution_id].argv[0] == "python3"
    assert observer.commands[output.execution_id].cwd == "/testbed"


@pytest.mark.parametrize(
    "mode",
    [
        "timeout",
        "cancel",
        "open_failure",
        "second_open_failure",
        "write_failure",
        "finish_failure",
    ],
)
async def test_real_docker_partial_and_capture_faults(
    docker_output_case, monkeypatch, mode
):
    workspace, artifacts = docker_output_case
    observer = MemoryExecutionObserver()
    if mode in {"open_failure", "second_open_failure"}:
        original = tempfile.mkstemp
        opens = 0

        def fail_open(*args, **kwargs):
            nonlocal opens
            if Path(kwargs.get("dir", ".")).name == "staging":
                opens += 1
                if opens == (2 if mode == "second_open_failure" else 1):
                    raise OSError("staging unavailable")
            return original(*args, **kwargs)

        monkeypatch.setattr(tempfile, "mkstemp", fail_open)
    elif mode == "write_failure":
        original = os.write

        def fail_write(descriptor, data):
            if len(data) > 1:
                raise OSError("capture write failed")
            return original(descriptor, data)

        monkeypatch.setattr(os, "write", fail_write)
    elif mode == "finish_failure":

        def fail_fsync(descriptor):
            raise OSError("capture publication failed")

        monkeypatch.setattr(os, "fsync", fail_fsync)
    script = (
        "import os,time;os.write(1,b'prefix');time.sleep(30)"
        if mode in {"timeout", "cancel"}
        else "import os;os.write(1,bytes(range(256))*4096)"
    )
    with execution_scope(observer):
        task = asyncio.create_task(
            workspace.execute(
                Command(
                    argv=["python3", "-c", script],
                    timeout_seconds=5 if mode == "timeout" else 30,
                )
            )
        )
        if mode == "cancel":
            deadline = monotonic() + 15
            while not any(
                path.stat().st_size >= 6
                for path in (artifacts.root / "staging").glob("*.tmp")
            ):
                assert monotonic() < deadline, "business output window not reached"
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == "timeout":
            assert (await task).status.value == "timeout"
        else:
            with pytest.raises(ArtifactPersistenceError):
                await task
    if mode in {"timeout", "cancel"}:
        output = next(iter(observer.outputs.values()))
        assert output.stdout.status == "partial"
        assert not output.stdout.eof_observed
        assert output.stdout.ref is not None
        assert await artifacts.read(output.stdout.ref) == b"prefix"
    if mode in {"open_failure", "second_open_failure"}:
        assert not observer.identities
    assert not list((artifacts.root / "staging").glob("*.tmp"))


@pytest.mark.parametrize(
    "mode", ["normal", "output_commit_failure", "cancel_before_permit"]
)
async def test_real_docker_tool_commands_and_durable_boundary(
    docker_output_case, tmp_path, monkeypatch, mode
):
    import sqlite3

    from miniagent.runtime import AgentRuntime
    from miniagent.runtime.events import (
        OperationExecutionIdentified,
        OperationOutputRecorded,
    )
    from miniagent.runtime.recording import RunPersistenceError
    from miniagent.storage import SQLiteRunStore
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import (
        AllowPolicyEngine,
        QueueProvider,
        SuccessfulTool,
        agent,
        final_response,
        tool_response,
    )
    from tests.support.trajectory.barriers import SQLiteWriteBarrier

    workspace, artifacts = docker_output_case
    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()

    class Tool(SuccessfulTool):
        async def execute(self, arguments, *, tool_call_id):
            await workspace.execute(
                Command(
                    argv=[
                        "python3",
                        "-c",
                        "from pathlib import Path;Path('first_effect').write_text('executed');print('full')",
                    ],
                    timeout_seconds=30,
                )
            )
            await workspace.execute(
                Command(
                    argv=[
                        "python3",
                        "-c",
                        "from pathlib import Path;Path('second_effect').write_text('executed')",
                    ],
                    timeout_seconds=30,
                )
            )
            return await super().execute(arguments, tool_call_id=tool_call_id)

    provider = QueueProvider([tool_response(), final_response()])
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([Tool()]),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
    )
    if mode == "output_commit_failure":
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_output AFTER INSERT ON run_events WHEN NEW.type='operation_output_recorded' BEGIN SELECT RAISE(ABORT,'output commit fault'); END"
            )
    barrier = None
    if mode == "cancel_before_permit":
        barrier = SQLiteWriteBarrier(
            "operation_execution_identified", command_only=True
        )
        monkeypatch.setattr(sqlite3, "connect", barrier.connector(sqlite3.connect))
    task = asyncio.create_task(
        runtime.run(agent(allowed_tools=["ok"]), "task", run_id=workspace.run_id)
    )
    if barrier is not None:
        try:
            assert await asyncio.to_thread(barrier.entered.wait, 10), (
                "guest command boundary not reached"
            )
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            barrier.release.set()
    if mode == "normal":
        assert (await task).output == "finished"
    else:
        with pytest.raises(RunPersistenceError):
            await task
        assert len(provider.requests) == 1
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "cp",
                f"{workspace.container_id}:/testbed/second_effect",
                str(tmp_path / "second_effect"),
            ],
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
    events = await store.list_events(workspace.run_id)
    commands = [
        event.payload.command
        for event in events
        if isinstance(event.payload, OperationExecutionIdentified)
        and event.payload.command is not None
    ]
    outputs = [
        event.payload.output
        for event in events
        if isinstance(event.payload, OperationOutputRecorded)
    ]
    if mode == "normal":
        assert len(commands) == len(outputs) == 2
        assert outputs[0].attempt_id == outputs[1].attempt_id
        assert outputs[0].execution_id != outputs[1].execution_id
        assert outputs[0].stdout.ref is not None
        assert await artifacts.read(outputs[0].stdout.ref) == b"full\n"
    elif mode == "output_commit_failure":
        assert len(commands) == 1 and not outputs
        assert (
            len((await artifacts.list_records_for_run(workspace.run_id)).records) == 2
        )
    else:
        assert len(commands) == 1 and not outputs
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "docker",
                "cp",
                f"{workspace.container_id}:/testbed/first_effect",
                str(tmp_path / "first_effect"),
            ],
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert (await artifacts.list_records_for_run(workspace.run_id)).records == ()
