from pathlib import Path

import httpx
import pytest

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings


async def test_host_origin_and_methods(database: Path, tmp_path: Path) -> None:
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        assert (await client.get("/")).status_code == 307
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (
            await client.get("/runs", headers={"Host": "evil.test:8080"})
        ).status_code == 403
        assert (
            await client.get("/runs", headers={"Origin": "http://localhost:8080"})
        ).status_code == 403
        assert (
            await client.get("/runs", headers={"Sec-Fetch-Site": "cross-site"})
        ).status_code == 403
        assert (
            await client.get("/runs", headers={"Origin": "http://127.0.0.1:8080"})
        ).status_code == 200
        assert (await client.post("/runs")).status_code == 405
        head = await client.head("/runs")
        assert head.status_code == 200
        assert head.content == b""
        for path in ["/health", "/runs", "/missing", "/static/dashboard.css"]:
            response = await client.get(path)
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "object-src 'none'" in response.headers["content-security-policy"]


async def test_error_responses_never_echo_input(
    database: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    sentinel = "sk-secret-sentinel-1234567890"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        for query in [
            f"task={sentinel}&unknown={sentinel}",
            f"page_size={sentinel}",
            f"cursor={sentinel}",
        ]:
            response = await client.get("/runs?" + query)
            assert response.status_code == 422
            assert sentinel not in response.text
    assert sentinel not in caplog.text
