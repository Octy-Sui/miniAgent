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
    LocalWorkspaceState,
)
from miniagent.environments.managed_execution import (
    MemoryExecutionObserver,
    execution_scope,
)


@pytest.mark.parametrize("variant", ["binary", "utf8", "empty", "both_empty"])
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
    stderr_expected = b"" if variant == "both_empty" else b"stderr"
    observer = MemoryExecutionObserver()
    command = Command(
        argv=[
            sys.executable,
            "-c",
            f"import sys; sys.stdout.buffer.write({expression}); sys.stderr.buffer.write({stderr_expected!r})",
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
    assert await store.read(output.stderr.ref) == stderr_expected
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
    if variant == "timeout":
        assert workspace.state is LocalWorkspaceState.ACTIVE
        assert (
            await workspace.execute(
                Command(argv=["/bin/sh", "-c", "printf after_timeout"])
            )
        ).stdout == "after_timeout"
    else:
        assert workspace.state is LocalWorkspaceState.BROKEN
    output = next(iter(observer.outputs.values()))
    assert output.stdout.status == "partial"
    assert not output.stdout.eof_observed
    assert output.stdout.reason is not None
    assert output.stdout.ref is not None
    assert (await store.read(output.stdout.ref)) in (b"", b"prefix")
    assert observer.evidence[output.execution_id].group_ended


@pytest.mark.parametrize(
    "mode",
    [
        "normal",
        "cancel_before_permit",
        "open_failure",
        "second_open_failure",
        "write_failure",
        "finish_failure",
        "manifest_failure",
        "combined_failure",
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
        artifact_store=artifacts,
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
    elif mode in {"write_failure", "combined_failure"}:
        write = os.write

        def fail_write(descriptor, data):
            if len(data) > 1:
                if mode == "combined_failure":
                    with sqlite3.connect(store.path) as connection:
                        connection.execute(
                            "CREATE TRIGGER fail_after_disk BEFORE INSERT ON run_events BEGIN SELECT RAISE(ABORT,'database unavailable after disk fault'); END"
                        )
                raise OSError("disk full")
            return write(descriptor, data)

        monkeypatch.setattr(os, "write", fail_write)
    elif mode == "finish_failure":

        def fail_fsync(descriptor):
            raise OSError("body publish unavailable")

        monkeypatch.setattr(os, "fsync", fail_fsync)
    elif mode == "manifest_failure":
        replace = os.replace

        def fail_manifest(source, target):
            if Path(target).suffix == ".json":
                assert any(path.is_file() for path in artifacts.content_root.rglob("*"))
                raise OSError("manifest publication failed")
            return replace(source, target)

        monkeypatch.setattr(os, "replace", fail_manifest)
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
            if mode
            in {"cancel_before_permit", "output_commit_failure", "combined_failure"}
            else ArtifactPersistenceError
        ) as failure:
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
    if mode == "combined_failure":
        assert any(
            "ArtifactPersistenceError" in note
            for note in getattr(failure.value, "__notes__", ())
        )
        with (
            sqlite3.connect(store.path) as connection,
            pytest.raises(sqlite3.IntegrityError, match="database unavailable"),
        ):
            connection.execute(
                "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json) VALUES(?,99999,?,3,'run_started','2026-10-20T00:00:00Z','{}','[]')",
                (str(run_id), str(uuid4())),
            )
    assert not list((artifacts.root / "staging").glob("*.tmp"))


@pytest.mark.parametrize("stage", ["before_finish", "during_body"])
async def test_cleanup_deadline_requested_after_capture_open(
    tmp_path, monkeypatch, stage
):
    import os
    import threading
    from time import monotonic

    from miniagent.environments.managed_execution import cleanup_deadline_scope
    from miniagent.environments.output_capture import OutputCapture

    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    deadline: list[float | None] = [None]
    entered, release = threading.Event(), threading.Event()
    fsync = os.fsync

    def blocked_fsync(descriptor):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("publish barrier not released")
        fsync(descriptor)

    with cleanup_deadline_scope(lambda: deadline[0]):
        capture = await OutputCapture.open(
            store=store, run_id=run_id, attempt_id=uuid4(), deadline=None
        )
        await capture.write("stdout", b"prefix")
        deadline[0] = monotonic() + (0.1 if stage == "during_body" else -1)
        if stage == "during_body":
            monkeypatch.setattr(os, "fsync", blocked_fsync)
        task = asyncio.create_task(
            capture.finish(
                execution_id=uuid4(), stdout_eof=True, stderr_eof=True, reason=None
            )
        )
        if stage == "during_body":
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                await asyncio.sleep(0.11)
            finally:
                release.set()
        with pytest.raises(TimeoutError, match="deadline"):
            await task
    assert (await store.list_records_for_run(run_id)).records == ()
    assert not list((store.root / "staging").glob("*.tmp"))


async def test_dual_stream_backpressure_preserves_all_bytes(
    tmp_path, source_repository, monkeypatch
):
    import os
    import time

    artifacts = FileArtifactStore(tmp_path / "artifacts")
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=artifacts, max_output_bytes=1024
    )
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    write = os.write

    def slow_write(descriptor, data):
        if len(data) > 1:
            time.sleep(0.001)
        return write(descriptor, data)

    monkeypatch.setattr(os, "write", slow_write)
    observer = MemoryExecutionObserver()
    with execution_scope(observer):
        result = await workspace.execute(
            Command(
                argv=[
                    sys.executable,
                    "-c",
                    "import os;[(os.write(1,b'A'*32768),os.write(2,b'B'*32768)) for _ in range(128)]",
                ],
                timeout_seconds=30,
            )
        )
    output = result.output_evidence
    assert (
        output is not None
        and output.stdout.ref is not None
        and output.stderr.ref is not None
    )
    assert await artifacts.read(output.stdout.ref) == b"A" * 4194304
    assert await artifacts.read(output.stderr.ref) == b"B" * 4194304
    assert result.stdout_truncated and result.stderr_truncated
    assert output.stdout.status == output.stderr.status == "complete"


async def test_read_file_evidence_uses_received_bytes_after_source_changes(
    tmp_path, source_repository, monkeypatch
):
    import json
    import os

    from miniagent.tools.read_file import ReadFileArguments, ReadFileTool

    artifacts = FileArtifactStore(tmp_path / "artifacts")
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=artifacts, max_output_bytes=1024
    )
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    source = workspace.root / "evidence-source.txt"
    original = b"original-line\n" * 10000
    source.write_bytes(original)
    replace = os.replace
    changed = False

    def replace_after_mutation(temporary, target):
        nonlocal changed
        if Path(target).suffix == ".json" and not changed:
            record = json.loads(Path(temporary).read_text())
            if record["type"] == "command_stdout":
                source.write_text("changed after read")
                changed = True
        return replace(temporary, target)

    monkeypatch.setattr(os, "replace", replace_after_mutation)
    observer = MemoryExecutionObserver()
    with execution_scope(observer):
        result = await ReadFileTool(workspace).execute(
            ReadFileArguments(path=source.name), tool_call_id="read"
        )
    assert changed and source.read_text() == "changed after read"
    stdout = next(ref for ref in result.artifacts if ref.type == "command_stdout")
    assert await artifacts.read(stdout) == original
    assert result.content.startswith(original[:1024].decode())
    assert result.output.stdout_truncated
    assert len(observer.commands) == len(observer.outputs) == 1


async def test_capture_stops_new_writes_after_late_cleanup_deadline(
    tmp_path, monkeypatch
):
    import os
    from time import monotonic
    from uuid import uuid4

    from miniagent.environments.managed_execution import cleanup_deadline_scope
    from miniagent.environments.output_capture import OutputCapture

    deadline = [None]
    writes = []
    write = os.write

    def expire_after_write(fd, content):
        writes.append(len(content))
        written = write(fd, content)
        deadline[0] = monotonic() - 1
        return written

    monkeypatch.setattr(os, "write", expire_after_write)
    with cleanup_deadline_scope(lambda: deadline[0]):
        capture = await OutputCapture.open(
            store=FileArtifactStore(tmp_path / "artifacts"),
            run_id=uuid4(),
            attempt_id=uuid4(),
            deadline=None,
        )
        try:
            await capture.write("stdout", b"first chunk")
            with pytest.raises(TimeoutError, match="deadline"):
                await capture.write("stdout", b"must not start")
        finally:
            await capture.abort()
    assert writes == [len(b"first chunk")]
