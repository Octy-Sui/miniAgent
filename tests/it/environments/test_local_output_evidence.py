import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    Command,
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
)
from miniagent.environments.managed_execution import (
    MemoryExecutionObserver,
    execution_scope,
)


@pytest.mark.parametrize("variant", ["binary", "utf8", "empty"])
async def test_tc_u09_01_to_04(
    tmp_path: Path, source_repository: Path, variant: str
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=store, max_output_bytes=1024
    )
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
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
    command = Command(
        argv=[
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({expression}); sys.stderr.buffer.write(b'stderr')",
        ],
        environment={"SECRET": "environment-sentinel"},
        stdin="stdin-sentinel",
    )
    with execution_scope(observer):
        result = await workspace.execute(command)
    output = result.output_evidence
    assert output is not None
    assert output.attempt_id == observer.attempt_id
    assert output.stdout.ref is not None
    assert output.stderr.ref is not None
    assert await store.read(output.stdout.ref) == expected
    assert await store.read(output.stderr.ref) == b"stderr"
    assert output.stdout.received_bytes == len(expected)
    assert output.stdout.status == "complete"
    assert output.stdout.eof_observed
    assert result.stdout_truncated == (len(expected) > 1024)
    assert len(result.stdout.encode()) < 5000
    description = observer.commands[output.execution_id]
    assert description.argv == tuple(command.argv)
    assert description.cwd == str(workspace.root)
    assert "environment-sentinel" not in description.model_dump_json()
    assert "stdin-sentinel" not in description.model_dump_json()
    assert observer.outputs[output.execution_id] == output
    await workspace.destroy()
    assert await FileArtifactStore(store.root).read(output.stdout.ref) == expected


@pytest.mark.parametrize("variant", ["timeout", "cancel"])
async def test_tc_u12_05_partial_stream(
    tmp_path: Path, source_repository: Path, variant: str
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    backend = LocalWorkspaceBackend(root=tmp_path / "workspaces", artifact_store=store)
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    observer = MemoryExecutionObserver()
    script = "import os,time;os.write(1,b'prefix');open('started','w').close();time.sleep(30)"
    with execution_scope(observer):
        task = asyncio.create_task(
            workspace.execute(
                Command(
                    argv=[sys.executable, "-c", script],
                    timeout_seconds=0.2 if variant == "timeout" else 30,
                )
            )
        )
        while not (workspace.root / "started").exists():
            await asyncio.sleep(0.01)
        if variant == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert (await task).status.value == "timeout"
    output = next(iter(observer.outputs.values()))
    assert output.stdout.status == "partial"
    assert not output.stdout.eof_observed
    assert output.stdout.reason is not None
    assert output.stdout.ref is not None
    assert (await store.read(output.stdout.ref)) in (b"", b"prefix")


@pytest.mark.parametrize(
    "mode",
    [
        "normal",
        "cancel_before_permit",
        "open_failure",
        "second_open_failure",
        "write_failure",
        "finish_failure",
        "output_commit_failure",
    ],
)
async def test_command_is_persisted_before_permission_and_failures_stop_next_command(
    tmp_path: Path, source_repository: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    import os
    import sqlite3
    import tempfile

    from miniagent.artifacts import ArtifactPersistenceError
    from miniagent.runtime import AgentRuntime
    from miniagent.runtime.events import (
        OperationExecutionEnded,
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

    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=artifacts, max_output_bytes=1024
    )
    run_id = uuid4()
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=run_id, options=LocalOptions(source_dir=source_repository)
        )
    )
    script = (
        "import sqlite3,json,pathlib,os;"
        f"connection=sqlite3.connect({str(store.path)!r});"
        "row=connection.execute(\"SELECT payload_json FROM run_events WHERE type='operation_execution_identified' ORDER BY sequence DESC LIMIT 1\").fetchone();"
        f"assert json.loads(row[0])['command']['cwd']=={str(workspace.root)!r};"
        "pathlib.Path('first_effect').write_text('executed');os.write(1,bytes(range(256))*1024)"
    )

    class Tool(SuccessfulTool):
        async def execute(self, arguments, *, tool_call_id):
            await workspace.execute(Command(argv=[sys.executable, "-c", script]))
            await workspace.execute(
                Command(
                    argv=[
                        sys.executable,
                        "-c",
                        "from pathlib import Path;Path('second_effect').write_text('executed')",
                    ]
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
    if mode in {"open_failure", "second_open_failure"}:
        mkstemp = tempfile.mkstemp
        opens = 0

        def fail_open(*args, **kwargs):
            nonlocal opens
            if Path(kwargs.get("dir", ".")).name == "staging":
                opens += 1
                if opens == (2 if mode == "second_open_failure" else 1):
                    raise OSError("staging unavailable")
            return mkstemp(*args, **kwargs)

        monkeypatch.setattr(tempfile, "mkstemp", fail_open)
    elif mode == "write_failure":
        write = os.write

        def fail_write(descriptor, data):
            if len(data) > 1:
                raise OSError("disk full")
            return write(descriptor, data)

        monkeypatch.setattr(os, "write", fail_write)
    elif mode == "finish_failure":

        def fail_fsync(descriptor):
            raise OSError("body publish unavailable")

        monkeypatch.setattr(os, "fsync", fail_fsync)
    elif mode == "output_commit_failure":
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_output AFTER INSERT ON run_events WHEN NEW.type='operation_output_recorded' BEGIN SELECT RAISE(ABORT,'output commit fault'); END"
            )
    barrier = None
    if mode == "cancel_before_permit":
        barrier = SQLiteWriteBarrier("operation_execution_identified")
        monkeypatch.setattr(sqlite3, "connect", barrier.connector(sqlite3.connect))
    task = asyncio.create_task(
        runtime.run(agent(allowed_tools=["ok"]), "task", run_id=run_id)
    )
    if barrier is not None:
        try:
            assert await asyncio.to_thread(barrier.entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            barrier.release.set()
    if mode == "normal":
        assert (await task).output == "finished"
    else:
        with pytest.raises(
            RunPersistenceError
            if mode in {"cancel_before_permit", "output_commit_failure"}
            else ArtifactPersistenceError
        ):
            await task
        assert not (workspace.root / "second_effect").exists()
        assert len(provider.requests) == 1
    events = await store.list_events(run_id)
    commands = [
        event
        for event in events
        if isinstance(event.payload, OperationExecutionIdentified)
    ]
    outputs = [
        event for event in events if isinstance(event.payload, OperationOutputRecorded)
    ]
    if mode == "normal":
        assert len(commands) == len(outputs) == 2
        facts = [
            event.payload.output
            for event in events
            if isinstance(event.payload, OperationOutputRecorded)
        ]
        assert len({output.execution_id for output in facts}) == 2
        assert len({output.attempt_id for output in facts}) == 1
        assert all(
            command.sequence < output.sequence
            for command, output in zip(commands, outputs, strict=True)
        )
        assert facts[0].stdout.ref is not None
        assert await artifacts.read(facts[0].stdout.ref) == bytes(range(256)) * 1024
    elif mode in {"cancel_before_permit", "open_failure", "second_open_failure"}:
        assert not (workspace.root / "first_effect").exists()
        assert not outputs
    elif mode == "output_commit_failure":
        assert not outputs
        assert len((await artifacts.list_records_for_run(run_id)).records) == 2
    elif mode == "write_failure":
        assert any(
            isinstance(event.payload, OperationExecutionEnded) for event in events
        )
    assert not list((artifacts.root / "staging").glob("*.tmp"))
