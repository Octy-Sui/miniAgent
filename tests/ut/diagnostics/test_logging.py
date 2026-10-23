import asyncio
import json
import logging

import pytest

from miniagent.cli.logging import LogSettings, logging_session
from miniagent.diagnostics import log_context, log_event, operation


def test_session_writes_safe_structured_context_and_preserves_stdout(tmp_path, capsys):
    with logging_session(LogSettings(log_dir=tmp_path)), log_context(run_id="run-one"):
        log_event(
            "model.requested", model="test-model", prompt="secret", api_key="secret"
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "model.requested" in captured.err
    assert "run-one" in captured.err
    records = [
        json.loads(line)
        for path in tmp_path.rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    event = next(record for record in records if record["event"] == "model.requested")
    assert event["run_id"] == "run-one"
    assert event["model"] == "test-model"
    assert "secret" not in json.dumps(records)


def test_nested_session_does_not_duplicate_or_remove_host_handlers(tmp_path):
    logger = logging.getLogger("miniagent")
    handler = logging.NullHandler()
    logger.addHandler(handler)
    try:
        with (
            logging_session(LogSettings(log_dir=tmp_path)),
            logging_session(LogSettings(log_dir=tmp_path)),
        ):
            log_event("once")
        assert handler in logger.handlers
        records = [
            json.loads(line)
            for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text().splitlines()
        ]
        assert sum(record["event"] == "once" for record in records) == 1
    finally:
        logger.removeHandler(handler)


def test_unwritable_destination_falls_back_to_stderr(tmp_path, capsys):
    path = tmp_path / "file"
    path.write_text("occupied")
    with logging_session(LogSettings(log_dir=path)):
        log_event("still.running")
    assert "logging.file_unavailable" in capsys.readouterr().err


async def test_heartbeat_stops_on_cancellation_and_preserves_cancellation(caplog):
    caplog.set_level(logging.INFO, logger="miniagent")
    ready = asyncio.Event()

    async def work():
        async with operation("model", interval_seconds=0.005):
            ready.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await ready.wait()
    async with asyncio.timeout(1):
        while not any(record.msg == "operation.waiting" for record in caplog.records):
            await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    count = len(caplog.records)
    await asyncio.sleep(0.02)
    assert len(caplog.records) == count
    assert not any(
        task.get_name().startswith("heartbeat:") for task in asyncio.all_tasks()
    )


async def test_disabled_heartbeat_and_original_exception(caplog):
    caplog.set_level(logging.INFO, logger="miniagent")
    with pytest.raises(ValueError, match="original"):
        async with operation("tool", interval_seconds=0):
            raise ValueError("original")
    assert not any(record.msg == "operation.waiting" for record in caplog.records)
    assert "original" not in caplog.text


async def test_parallel_run_contexts_are_isolated(caplog):
    caplog.set_level(logging.INFO, logger="miniagent")

    async def work(run_id):
        with log_context(run_id=run_id):
            async with operation("model", interval_seconds=0.001):
                await asyncio.sleep(0.01)

    await asyncio.gather(work("one"), work("two"))
    records = [record for record in caplog.records if record.msg == "operation.waiting"]
    assert {record.fields["run_id"] for record in records} == {"one", "two"}
    log_event("outside")
    assert "run_id" not in caplog.records[-1].fields


async def test_nested_operation_only_reports_innermost_phase(caplog):
    caplog.set_level(logging.INFO, logger="miniagent")
    async with (
        operation("evaluation", interval_seconds=0.002),
        operation("evaluation_harness", interval_seconds=0.002),
    ):
        await asyncio.sleep(0.015)
    records = [record for record in caplog.records if record.msg == "operation.waiting"]
    assert records
    assert {record.fields["phase"] for record in records} == {"evaluation_harness"}


def test_log_settings_read_shared_entrypoint_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MINIAGENT_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MINIAGENT_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("MINIAGENT_PROGRESS_INTERVAL_SECONDS", "0")
    settings = LogSettings()
    assert settings.log_dir == tmp_path
    assert settings.log_level == "WARNING"
    assert settings.progress_interval_seconds == 0


async def test_cancellation_arriving_during_heartbeat_cleanup_propagates():
    async def work():
        async with operation("model", interval_seconds=15):
            current = asyncio.current_task()
            assert current is not None
            asyncio.get_running_loop().call_soon(current.cancel)

    task = asyncio.create_task(work())
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(
        task.get_name().startswith("heartbeat:") for task in asyncio.all_tasks()
    )


async def test_overlapping_sessions_keep_separate_files_and_complete(tmp_path, capsys):
    first_open = asyncio.Event()
    second_open = asyncio.Event()
    first_closed = asyncio.Event()

    async def first():
        with logging_session(LogSettings(log_dir=tmp_path / "first")):
            first_open.set()
            await second_open.wait()
            log_event("first.only")
        first_closed.set()

    async def second():
        await first_open.wait()
        with logging_session(LogSettings(log_dir=tmp_path / "second")):
            second_open.set()
            await first_closed.wait()
            log_event("second.only")

    await asyncio.gather(first(), second())
    for name, other in (("first", "second"), ("second", "first")):
        records = [
            json.loads(line)
            for path in (tmp_path / name).rglob("*.jsonl")
            for line in path.read_text().splitlines()
        ]
        names = [record["event"] for record in records]
        assert f"{name}.only" in names
        assert f"{other}.only" not in names
        assert names[-1] == "invocation.completed"
        assert len({record["invocation_id"] for record in records}) == 1
    stderr = capsys.readouterr().err
    assert stderr.count("first.only") == 1
    assert stderr.count("second.only") == 1


def test_broken_stderr_does_not_prevent_file_delivery(tmp_path, monkeypatch):
    class BrokenStream:
        def write(self, text):
            raise OSError("output device unavailable")

        def flush(self):
            raise OSError("output device unavailable")

    monkeypatch.setattr("sys.stderr", BrokenStream())
    with logging_session(LogSettings(log_dir=tmp_path)):
        log_event("business.completed")
    records = [
        json.loads(line)
        for path in tmp_path.rglob("*.jsonl")
        for line in path.read_text().splitlines()
    ]
    assert any(record["event"] == "business.completed" for record in records)
