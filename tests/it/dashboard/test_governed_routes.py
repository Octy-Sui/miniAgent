import asyncio
import json
import logging
import re
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from miniagent.dashboard.workers import HeavyWorkPool
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.events import ModelRequested, NewRunEvent
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import make_run, persist_memory
from tests.support.dashboard.observation import capture_read_only
from tests.support.dashboard.workspace_pages import boundary_token

RUN_ID = UUID(int=1)
SECRET = "sk-governed-secret-1234567890123456"
CONTROL = "visible-control"
DISPLAY = f"{CONTROL} <script>window.executable=1</script> token={SECRET}"


async def display_fixture(database: Path, root: Path):
    await persist_memory(database, display_text=DISPLAY)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_set(state_json,'$.identity.task',?,'$.lifecycle.candidate_output',?,'$.lifecycle.final_output',?)",
            (DISPLAY, DISPLAY, DISPLAY),
        )
        connection.commit()
    await SQLiteRunStore(database).append(
        NewRunEvent(
            run_id=RUN_ID,
            payload=ModelRequested(
                provider="test",
                model=DISPLAY,
                request=ModelRequest(model=DISPLAY, messages=()),
            ),
        ),
        expected_sequence=100,
    )
    store = FileArtifactStore(root)
    refs = {}
    for kind, body in [
        (
            "workspace_patch",
            f"diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+{DISPLAY}\n".encode(),
        ),
        ("evaluation_stdout", (DISPLAY + "\n").encode() * 2100),
        (
            "evaluation_report",
            json.dumps(
                {
                    "uri": "file:///private/sentinel-location",
                    "endpoint": "https://sentinel-connection.invalid/api",
                    "source_dir": "/private/sentinel-location",
                    "visible": DISPLAY,
                }
            ).encode(),
        ),
    ]:
        refs[kind] = await store.put(
            ArtifactWrite(
                run_id=RUN_ID,
                type=kind,
                media_type="application/json"
                if kind == "evaluation_report"
                else "text/plain",
                metadata={
                    "visible": DISPLAY,
                    "uri": "file:///private/sentinel-location",
                    "endpoint": "https://sentinel-connection.invalid/api",
                },
            ),
            body,
        )
    return refs


def security_headers(response: httpx.Response) -> None:
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    csp = response.headers["content-security-policy"]
    for name, value in [
        ("default-src", "'self'"),
        ("script-src", "'self'"),
        ("style-src", "'self'"),
        ("connect-src", "'self'"),
        ("object-src", "'none'"),
        ("frame-ancestors", "'none'"),
        ("base-uri", "'none'"),
    ]:
        assert f"{name} {value}" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


async def test_all_read_routes_preserve_business_data(
    database: Path, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    refs = await display_fixture(database, root)
    before = capture_read_only(database, root)
    statements = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    app = create_app(
        DashboardSettings(database=database, artifacts=root), connect=connect
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        detail = await client.get(f"/runs/{RUN_ID}")
        assert detail.status_code == 200
        token = boundary_token(detail.text)
        routes = [
            "/",
            "/runs",
            "/health",
            "/healthz",
            "/static/dashboard.css",
            "/static/dashboard.js",
            "/static/vendor/htmx.min.js",
            "/static/vendor/htmx.LICENSE",
            f"/runs/{RUN_ID}",
            f"/runs/{RUN_ID}/workspace",
            f"/runs/{RUN_ID}/events?boundary={token}",
            f"/runs/{RUN_ID}/snapshots/4?boundary={token}&start=2&end=3",
            *[
                f"/runs/{RUN_ID}/artifacts/{ref.artifact_id}?boundary={token}"
                for ref in refs.values()
            ],
        ]
        for route in routes:
            for method in ("GET", "HEAD"):
                response = await client.request(method, route)
                assert response.status_code in (200, 307), route
                security_headers(response)
                if method == "HEAD":
                    assert response.content == b""
        for route in (
            "/runs",
            f"/runs/{RUN_ID}",
            f"/runs/{RUN_ID}/workspace",
            f"/runs/{RUN_ID}/events?boundary={token}",
        ):
            response = await client.get(route, headers={"HX-Request": "true"})
            assert response.status_code == 200
            security_headers(response)
    after = capture_read_only(database, root)
    assert after == before
    forbidden = re.compile(
        r"^\s*(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|REPLACE|VACUUM|REINDEX|ATTACH)\b|PRAGMA\s+journal_mode",
        re.IGNORECASE,
    )
    assert not [statement for statement in statements if forbidden.search(statement)]


@pytest.mark.parametrize(
    "surface",
    [
        "list",
        "detail",
        "timeline",
        "snapshot",
        "memory",
        "metadata",
        "diff",
        "log",
        "json",
        "fragment",
    ],
)
async def test_each_display_surface_redacts_and_escapes(
    database: Path, tmp_path: Path, surface: str, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "artifacts"
    refs = await display_fixture(database, root)
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        first = await client.get(f"/runs/{RUN_ID}")
        token = boundary_token(first.text)
        paths = {
            "list": "/runs",
            "detail": f"/runs/{RUN_ID}",
            "memory": f"/runs/{RUN_ID}",
            "timeline": f"/runs/{RUN_ID}/events?boundary={token}",
            "snapshot": f"/runs/{RUN_ID}/snapshots/4?boundary={token}&start=2&end=3",
            "metadata": f"/runs/{RUN_ID}/workspace",
            "diff": f"/runs/{RUN_ID}/workspace",
            "fragment": f"/runs/{RUN_ID}",
            "log": f"/runs/{RUN_ID}/artifacts/{refs['evaluation_stdout'].artifact_id}?boundary={token}",
            "json": f"/runs/{RUN_ID}/artifacts/{refs['evaluation_report'].artifact_id}?boundary={token}",
        }
        response = await client.get(
            paths[surface],
            headers={"HX-Request": "true"} if surface == "fragment" else {},
        )
        assert response.status_code == 200
        assert CONTROL in response.text
        patterns = {
            "list": r'<section id="run-list".*?</section>',
            "timeline": r'<section id="timeline".*?</section>',
            "memory": r'<section id="memory".*?</section>',
            "metadata": r'<section id="artifact-metadata".*?</section>',
            "diff": r'<section id="patch-preview".*?</section>',
            "snapshot": r"<section><h2>Snapshot 消息范围</h2>.*?</section>",
            "log": r'<pre id="artifact-text">.*?</pre>',
            "json": r'<pre id="artifact-text">.*?</pre>',
        }
        if surface in patterns:
            scoped = re.search(patterns[surface], response.text, re.DOTALL)
            assert scoped is not None
            assert CONTROL in scoped[0]
        if surface in {"detail", "fragment"}:
            for label in ("任务", "候选输出", "最终输出"):
                scoped = re.search(
                    r"<dt>" + label + r"</dt><dd>.*?</dd>", response.text, re.DOTALL
                )
                assert scoped is not None
                assert CONTROL in scoped[0]
        assert SECRET not in response.text + str(response.headers)
        assert "<script>window.executable=1</script>" not in response.text
        if surface in {"metadata", "diff", "json"}:
            assert "sentinel-location" not in response.text
            assert "sentinel-connection" not in response.text
        assert not re.search(r'href="(?:file:|https?://)', response.text)
    assert SECRET not in "".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("miniagent.dashboard")
    )


@pytest.mark.parametrize(
    ("path", "method", "headers", "expected"),
    [
        ("/runs/not-a-uuid", "GET", {}, 422),
        ("/runs?bad=sk-query-secret-sentinel", "GET", {}, 422),
        ("/runs?cursor=sk-query-secret-sentinel", "HEAD", {}, 422),
        ("/runs/00000000-0000-0000-0000-000000009999", "GET", {}, 404),
        ("/runs", "POST", {}, 405),
        ("/runs", "GET", {"Host": "127.0.0.1.evil:8080"}, 403),
        ("/runs", "HEAD", {"Origin": "http://evil.invalid"}, 403),
        ("/static/missing.css", "GET", {}, 404),
    ],
)
async def test_each_http_failure_has_its_own_semantics(
    database: Path,
    tmp_path: Path,
    path: str,
    method: str,
    headers: dict[str, str],
    expected: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.request(method, path, headers=headers)
        assert response.status_code == expected
        assert (
            "sk-query-secret-sentinel"
            not in response.text + str(response.headers) + caplog.text
        )
        security_headers(response)
        if method == "HEAD":
            assert response.content == b""


async def test_corrupt_database_returns_error_id_without_diagnostic_leaks(
    database: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await make_run(database, 1)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    caplog.set_level(logging.ERROR, logger="miniagent.dashboard.app")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        database.rename(database.with_suffix(".original"))
        database.write_bytes(b"corrupt-sentinel-host-path /private/diagnostic-location")
        response = await client.get("/runs?task=sk-query-secret-sentinel")
        assert response.status_code == 500
        error = re.search(r"错误 ID：<code>([0-9a-f]{16})</code>", response.text)
        assert error is not None
        assert error[1] in caplog.text
        for sentinel in (
            "sk-query-secret-sentinel",
            "corrupt-sentinel-host-path",
            "/private/diagnostic-location",
            str(database),
        ):
            assert sentinel not in response.text + caplog.text
        security_headers(response)


async def test_registered_missing_and_oversized_content_have_distinct_statuses(
    database: Path, tmp_path: Path
) -> None:
    await make_run(database, 1)
    root = tmp_path / "artifacts"
    store = FileArtifactStore(root)
    ref = await store.put(
        ArtifactWrite(run_id=RUN_ID, type="evaluation_stdout", media_type="text/plain"),
        b"visible-control",
    )
    record = await store.get_record(ref)
    target = root / "content" / record.sha256[:2] / record.sha256
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        token = boundary_token((await client.get(f"/runs/{RUN_ID}")).text)
        url = f"/runs/{RUN_ID}/artifacts/{ref.artifact_id}"
        missing = await client.get(
            f"/runs/{RUN_ID}/artifacts/{UUID(int=999)}", params={"boundary": token}
        )
        assert missing.status_code == 404
        target.unlink()
        missing_bytes = await client.get(url, params={"boundary": token})
        assert missing_bytes.status_code == 409
        with target.open("wb") as stream:
            stream.truncate(20 * 1024 * 1024 + 1)
        limited = await client.get(url, params={"boundary": token})
        assert limited.status_code == 200
        assert "超出首版读取上限" in limited.text
        for response in (missing, missing_bytes, limited):
            security_headers(response)


async def test_unknown_stored_schema_is_a_readable_placeholder(
    database: Path, tmp_path: Path
) -> None:
    await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE runs SET schema_version=99,last_event_sequence=1")
        connection.execute(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json) VALUES(?,1,?,99,'unknown_event','2026-10-19T00:00:00Z','{}','[]')",
            (str(RUN_ID), str(UUID(int=2))),
        )
        connection.commit()
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(f"/runs/{RUN_ID}")
        assert response.status_code == 200
        assert "正文未完成读取或不支持" in response.text
        assert 'data-event-sequence="1"' in response.text
        assert str(RUN_ID) in response.text


async def test_unsupported_database_schema_is_not_initialized(
    database: Path, tmp_path: Path
) -> None:
    from miniagent.dashboard.reads import ReadFailure

    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA user_version=99")
    before = capture_read_only(database, tmp_path / "artifacts")
    app = create_app(
        DashboardSettings(database=database, artifacts=tmp_path / "artifacts")
    )
    with pytest.raises(ReadFailure, match="database_unsupported"):
        async with app.router.lifespan_context(app):
            raise AssertionError("unsupported database was accepted")
    assert capture_read_only(database, tmp_path / "artifacts") == before


async def test_heavy_busy_is_503_while_health_remains_readable(
    database: Path, tmp_path: Path
) -> None:
    await make_run(database, 1)
    pool = HeavyWorkPool(limit=1)
    app = create_app(
        DashboardSettings(database=database, artifacts=tmp_path), work_pool=pool
    )
    started = threading.Event()
    release = threading.Event()
    source = tmp_path / "source.json"
    source.write_text('{"visible":true}')

    def actual_read():
        started.set()
        assert release.wait(3)
        return json.loads(source.read_text())

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        held = asyncio.create_task(pool.run(actual_read))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            busy = await client.get("/runs")
            assert busy.status_code == 503
            security_headers(busy)
            assert (await client.get("/health")).status_code == 200
        finally:
            release.set()
            assert await held == {"visible": True}


async def test_expired_read_budget_is_503(database: Path, tmp_path: Path) -> None:
    await make_run(database, 1)
    values = iter([0.0, 10.0])
    app = create_app(
        DashboardSettings(database=database, artifacts=tmp_path),
        monotonic=lambda: next(values, 10.0),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get("/runs")
        assert response.status_code == 503
        security_headers(response)


async def test_all_supported_status_filters_are_selectable(
    database: Path, tmp_path: Path
) -> None:
    from miniagent.runtime.state import RunStatus

    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get("/runs")
        assert response.status_code == 200
        for status in RunStatus:
            assert f'<option value="{status}"' in response.text
