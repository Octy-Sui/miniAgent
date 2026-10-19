import asyncio
import json
import multiprocessing
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from miniagent.dashboard.json_content import parse_json
from miniagent.dashboard.previews import project_preview
from miniagent.dashboard.projections.compaction import project_memory
from miniagent.dashboard.projections.evaluation import project_evaluations
from miniagent.dashboard.projections.events import project_events
from miniagent.dashboard.projections.run import project_run
from miniagent.dashboard.projections.snapshots import project_snapshot
from miniagent.dashboard.projections.workspace import parse_diff
from miniagent.dashboard.reads import ReadBudget
from miniagent.dashboard.redaction import redact_json, redact_text
from miniagent.storage.codecs import decode_run, decode_snapshot
from tests.dashboard.builders import make_run
from tests.dashboard.observation import (
    RecordingConnection,
    RecordingOpen,
    RecordingPool,
    StageRecorder,
)
from tests.dashboard.test_detail_pages import populated
from tests.dashboard.test_workspace_pages import boundary_token, publish_evaluation


def supervised(target, *args):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=target, args=(*args, child))
    process.start()
    child.close()
    return process, parent


def finish(process, channel):
    try:
        assert channel.poll(15), "product work exceeded supervisor deadline"
        result = channel.recv()
        process.join(2)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        channel.close()


def sql_probe(database: str, value, channel) -> None:
    observed = {"started": False, "interrupted": False}
    connections = []

    class Observed(RecordingConnection):
        def execute(self, sql: str, parameters=()):
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", 0) == sqlite3.SQLITE_INTERRUPT:
                    observed["interrupted"] = True
                raise

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, factory=Observed, **kwargs)
        connections.append(connection)

        def trace(sql: str) -> None:
            if "SELECT status,count(*) FROM base" in sql and not observed["started"]:
                observed["started"] = True
                channel.send({"sql_started": True, "thread": threading.get_ident()})
                assert channel.poll(10), "SQL handshake not released"
                assert channel.recv() == "continue"

        connection.set_trace_callback(trace)
        return connection

    async def run():
        pool = RecordingPool()
        app = create_app(
            DashboardSettings(database=Path(database)),
            connect=connect,
            monotonic=lambda: value.value,
            work_pool=pool,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client,
        ):
            response = await client.get("/runs?task=visible&model=test-model")
        return {
            "status": response.status_code,
            "observed": observed,
            "finished": len(pool.intervals),
            "active": pool.active,
            "connections_closed": all(
                connection.was_closed for connection in connections
            ),
        }

    channel.send(asyncio.run(run()))
    channel.close()


async def large_database(database: Path) -> None:
    await make_run(database, 1, task="visible task " + ("x" * 2048))
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT * FROM runs").fetchone()
        columns = [item[1] for item in connection.execute("PRAGMA table_info(runs)")]
        body_index = columns.index("state_json")
        id_index = columns.index("run_id")
        records = []
        for n in range(2, 1002):
            copy = list(row)
            copy[id_index] = str(UUID(int=n))
            state = json.loads(copy[body_index])
            state["identity"]["run_id"] = copy[id_index]
            copy[body_index] = json.dumps(state)
            records.append(copy)
        connection.executemany(
            "INSERT INTO runs VALUES(" + ",".join("?" for _ in row) + ")", records
        )
        connection.commit()


@pytest.mark.parametrize("deadline", [5.0, 5.001])
async def test_real_sql_is_interrupted_after_deadline(
    database: Path, deadline: float
) -> None:
    await large_database(database)
    context = multiprocessing.get_context("spawn")
    clock = context.Value("d", 0.0)
    process, channel = supervised(sql_probe, str(database), clock)
    try:
        assert channel.poll(15), "target SQL did not start"
        assert channel.recv()["sql_started"]
        clock.value = deadline
        channel.send("continue")
        result = finish(process, channel)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
    assert result["status"] == 503
    assert result["observed"]["interrupted"]
    assert result["finished"] >= 2 and result["active"] == 0
    assert result["connections_closed"]


async def test_real_sql_before_deadline_succeeds(database: Path) -> None:
    await large_database(database)
    context = multiprocessing.get_context("spawn")
    clock = context.Value("d", 0.0)
    process, channel = supervised(sql_probe, str(database), clock)
    try:
        assert channel.poll(15)
        assert channel.recv()["sql_started"]
        clock.value = 4.999
        channel.send("continue")
        result = finish(process, channel)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
    assert result["status"] == 200
    assert not result["observed"]["interrupted"]
    assert result["active"] == 0
    assert result["connections_closed"]


def stage_probe(database: str, root: str, log_id: str, channel) -> None:
    opening = RecordingOpen(Path(root))
    connections = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, factory=RecordingConnection, **kwargs)
        connections.append(connection)
        return connection

    recorder = StageRecorder(
        {
            function.__name__: function
            for function in (
                decode_run,
                decode_snapshot,
                parse_json,
                project_run,
                project_memory,
                project_events,
                project_snapshot,
                project_evaluations,
                parse_diff,
                project_preview,
                redact_text,
                redact_json,
            )
        }
    )
    asgi_thread = threading.get_ident()
    threading.setprofile_all_threads(recorder)
    calibration = threading.Thread(
        target=lambda: redact_text("Bearer calibration-secret")
    )
    calibration.start()
    calibration.join(2)
    assert any(
        item[0] == "redact_text" and item[1] != asgi_thread for item in recorder.records
    )
    recorder.records.clear()

    async def run():
        pool = RecordingPool()
        app = create_app(
            DashboardSettings(database=Path(database), artifacts=Path(root)),
            connect=connect,
            open_fd=opening,
            monotonic=lambda: 0,
            work_pool=pool,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client,
        ):
            detail = await client.get(f"/runs/{UUID(int=1)}")
            token = boundary_token(detail.text)
            snapshot = await client.get(
                f"/runs/{UUID(int=1)}/snapshots/2",
                params={"boundary": token, "start": 0, "end": 1},
            )
            workspace = await client.get(f"/runs/{UUID(int=1)}/workspace")
            preview = await client.get(
                f"/runs/{UUID(int=1)}/artifacts/{log_id}", params={"boundary": token}
            )
        return {
            "statuses": [
                response.status_code
                for response in (detail, snapshot, workspace, preview)
            ],
            "intervals": pool.intervals,
            "peak": pool.peak,
            "active": pool.active,
        }

    try:
        result = asyncio.run(run())
    finally:
        threading.setprofile_all_threads(None)
    channel.send(
        {
            **result,
            "stages": recorder.records,
            "asgi_thread": asgi_thread,
            "connections_closed": all(
                connection.was_closed for connection in connections
            ),
            "live_fds": opening.live_descriptors(),
            "threads": [
                thread.name
                for thread in threading.enumerate()
                if thread is not threading.main_thread()
            ],
        }
    )
    channel.close()


async def test_heavy_stages_run_inside_worker_slot(
    database: Path, tmp_path: Path
) -> None:
    await populated(database)
    root = tmp_path / "artifacts"
    records = await publish_evaluation(database, root, persist_run=False)
    process, channel = supervised(
        stage_probe, str(database), str(root), str(records[107].artifact_id)
    )
    result = finish(process, channel)
    assert result["statuses"] == [200] * 4
    expected = {
        "decode_run",
        "decode_snapshot",
        "parse_json",
        "project_run",
        "project_memory",
        "project_events",
        "project_snapshot",
        "project_evaluations",
        "parse_diff",
        "project_preview",
        "hash.update",
        "redact_text",
        "redact_json",
    }
    assert expected <= {item[0] for item in result["stages"]}
    assert any(
        item[0] == "parse_json" and item[4] == "adapt_evaluation"
        for item in result["stages"]
    )
    for _, thread, start, end, _ in result["stages"]:
        assert thread != result["asgi_thread"]
        assert any(
            thread == slot_thread and slot_start <= start <= end <= slot_end
            for slot_thread, slot_start, slot_end in result["intervals"]
        )
    assert result["peak"] <= 2 and result["active"] == 0
    assert result["connections_closed"]
    assert result["live_fds"] == result["threads"] == []


def cancellation_probe(database: str, channel) -> None:
    starts = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    thread_ids = []
    count_lock = threading.Lock()

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, factory=RecordingConnection, **kwargs)
        paused = False

        def trace(sql: str) -> None:
            nonlocal paused
            if "SELECT status,count(*) FROM base" not in sql or paused:
                return
            paused = True
            with count_lock:
                index = len(thread_ids)
                thread_ids.append(threading.get_ident())
            if index < 2:
                starts[index].set()
                if not releases[index].wait(5):
                    raise TimeoutError("worker handshake was not released")

        connection.set_trace_callback(trace)
        return connection

    async def run():
        pool = RecordingPool()
        app = create_app(
            DashboardSettings(database=Path(database)),
            connect=connect,
            monotonic=lambda: 0,
            work_pool=pool,
        )
        errors = []
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, context: errors.append(context.get("message"))
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client,
        ):
            mark = time.perf_counter_ns()
            tasks = [asyncio.create_task(client.get("/runs")) for _ in range(2)]
            try:
                for event in starts:
                    assert await asyncio.to_thread(event.wait, 2)
                light = await client.get("/health")
                third = await client.get("/runs")
                tasks[0].cancel()
                with pytest.raises(asyncio.CancelledError):
                    await tasks[0]
                after_cancel = await client.get("/runs")
                assert not any(start >= mark for _, start, _ in pool.intervals)
                releases[0].set()
                deadline = time.monotonic() + 3
                while not any(
                    thread == thread_ids[0] and start >= mark
                    for thread, start, _ in pool.intervals
                ):
                    assert time.monotonic() < deadline, "actual worker did not finish"
                    await asyncio.sleep(0.001)
                await asyncio.sleep(0)
                after_actual = await client.get("/runs")
                while after_actual.status_code == 503:
                    assert time.monotonic() < deadline, (
                        "worker completion was not delivered"
                    )
                    await asyncio.sleep(0.001)
                    after_actual = await client.get("/runs")
                return {
                    "light": light.status_code,
                    "third": third.status_code,
                    "cancelled": after_cancel.status_code,
                    "released": after_actual.status_code,
                    "peak": pool.peak,
                    "errors": errors,
                }
            finally:
                for event in releases:
                    event.set()
                await asyncio.gather(*tasks, return_exceptions=True)

    channel.send(asyncio.run(run()))
    channel.close()


async def test_cancelled_http_keeps_slot_until_worker_finishes(database: Path) -> None:
    await make_run(database, 1)
    process, channel = supervised(cancellation_probe, str(database))
    result = finish(process, channel)
    assert result["light"] == 200
    assert result["third"] == result["cancelled"] == 503
    assert result["released"] == 200
    assert result["peak"] == 2
    assert result["errors"] == []


def busy_probe(database: str, channel) -> None:
    overheads = []
    for _ in range(20):
        event = threading.Event()
        start = time.monotonic()
        thread = threading.Thread(target=event.set)
        thread.start()
        assert event.wait(1)
        thread.join(1)
        overheads.append(time.monotonic() - start)
    epsilon = max(0.1, 2 * max(overheads))

    async def run():
        app = create_app(DashboardSettings(database=Path(database)))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client,
        ):
            with closing(sqlite3.connect(database)) as writer:
                assert (
                    writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    == "delete"
                )
                writer.execute("BEGIN EXCLUSIVE")
                started = time.monotonic()
                blocked = await client.get("/runs")
                elapsed = time.monotonic() - started
                writer.rollback()
            after = await client.get("/runs")
        return {
            "epsilon": epsilon,
            "handshake_max": max(overheads),
            "blocked": blocked.status_code,
            "elapsed": elapsed,
            "after": after.status_code,
        }

    channel.send(asyncio.run(run()))
    channel.close()


async def test_real_busy_lock_times_out_and_recovers(database: Path) -> None:
    await make_run(database, 1)
    process, channel = supervised(busy_probe, str(database))
    result = finish(process, channel)
    assert result["blocked"] == 503
    assert 1 - result["epsilon"] <= result["elapsed"] <= 1 + result["epsilon"]
    print("busy-evidence", json.dumps(result, sort_keys=True))
    assert result["after"] == 200


def phase_probe(root: str, phase: str, crossing: float, channel) -> None:
    import os
    import sys

    from miniagent.dashboard.artifacts import ArtifactReader
    from miniagent.dashboard.reads import ReadFailure
    from tests.dashboard.observation import RecordingOpen

    opening = RecordingOpen(Path(root))
    clock = [0.0]
    started = []

    def profile(frame, event, arg):
        if phase == "directory" and event == "c_return" and arg is os.scandir:
            started.append("directory")
            clock[0] = crossing
        elif (
            phase == "file"
            and event == "c_return"
            and arg is os.read
            and opening.content_fd(frame.f_locals.get("descriptor"))
        ):
            started.append("file")
            clock[0] = crossing

    def monotonic() -> float:
        frame = sys._getframe(2)
        if (
            phase == "json"
            and frame.f_code is parse_json.__code__
            and frame.f_locals.get("index", 0) >= 65536
        ):
            started.append("json")
            clock[0] = crossing
        elif (
            phase == "diff"
            and frame.f_code is parse_diff.__code__
            and frame.f_locals.get("files")
        ):
            started.append("diff")
            clock[0] = crossing
        return clock[0]

    budget = ReadBudget(5, monotonic=monotonic)
    sys.setprofile(profile)
    try:
        if phase == "directory":
            result = ArtifactReader(Path(root), open_fd=opening).discover_patches(
                UUID(int=1), budget=budget
            )
            outcome = {"complete": result.complete}
        elif phase == "file":
            result = ArtifactReader(Path(root), open_fd=opening).read_content(
                UUID(int=1), UUID(int=2), budget=budget
            )
            outcome = {"complete": result.content is not None}
        elif phase == "json":
            parse_json('"' + ("x" * 70000) + '"', budget=budget)
            outcome = {"complete": True}
        else:
            result = parse_diff(
                "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
                budget=budget,
            )
            outcome = {"complete": result.complete}
    except ReadFailure as exc:
        outcome = {"status": exc.status_code}
    finally:
        sys.setprofile(None)
    channel.send({**outcome, "started": started})
    channel.close()


@pytest.mark.parametrize("phase", ["directory", "file", "json", "diff"])
@pytest.mark.parametrize("crossing", [4.999, 5.0, 5.001])
def test_each_heavy_loop_observes_deadline(
    tmp_path: Path, phase: str, crossing: float
) -> None:
    from tests.dashboard.test_manifests import manifest

    root = tmp_path / "artifacts"
    manifest(root, 2, "workspace_patch")
    process, channel = supervised(phase_probe, str(root), phase, crossing)
    result = finish(process, channel)
    assert result["started"]
    if crossing < 5:
        assert result["complete"]
    elif phase in {"directory", "diff"}:
        assert result["complete"] is False
    else:
        assert result["status"] == 503
