import asyncio
import signal

import pytest


@pytest.mark.parametrize(("sent",), [(signal.SIGINT,), (signal.SIGTERM,)])
@pytest.mark.parametrize(
    ("mode",),
    [
        ("before_record",),
        ("model",),
        ("finalization",),
        ("second",),
        ("summary",),
        ("shell",),
        ("attach",),
    ],
)
async def test_real_process_signal_covers_application_scope(tmp_path, mode, sent):
    import sqlite3
    import sys
    from uuid import UUID

    from miniagent.storage import SQLiteRunStore

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.cli.resume_signal_driver",
        mode,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output = []
    try:
        assert process.stdout is not None
        while (
            line := await asyncio.wait_for(process.stdout.readline(), 8)
        ) != b"ready\n":
            assert line
            output.append(line)
        process.send_signal(sent)
        if mode == "second":
            assert (
                await asyncio.wait_for(process.stdout.readline(), 5) == b"first_seen\n"
            )
            process.send_signal(sent)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
        output.append(stdout)
        assert process.returncode == 128 + sent.value, stderr.decode()
        if mode == "before_record":
            assert b"Resume:" not in b"".join(output)
            assert not list((tmp_path / "workspaces").rglob(".miniagent-workspace"))
        if mode in ("model", "finalization", "summary", "shell", "attach"):
            with sqlite3.connect(tmp_path / "runs.sqlite3") as connection:
                run_id = UUID(
                    connection.execute("select run_id from runs").fetchone()[0]
                )
            store = SQLiteRunStore(tmp_path / "runs.sqlite3", read_only=True)
            record = await store.get(run_id)
            assert record.state.lifecycle.status.value == (
                "succeeded" if mode == "finalization" else "suspended"
            )
            events = await store.list_events(run_id)
            if mode != "finalization":
                assert not any(
                    event.type.value in ("run_finished", "step_completed")
                    for event in events
                )
            from miniagent.environments.attachment import LocalAttachment

            if mode == "summary":
                assert record.state.execution.active_operation is not None
                assert record.state.execution.active_operation.kind == "summary"
                return
            assert record.recovery_context is not None
            assert isinstance(record.recovery_context.attachment, LocalAttachment)
            assert record.recovery_context.attachment.repository_root.is_dir()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
