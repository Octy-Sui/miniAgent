from pathlib import Path

import httpx

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from tests.dashboard.builders import make_run


async def test_list_page_shows_only_recorded_facts(
    database: Path, tmp_path: Path
) -> None:
    run_id = await make_run(
        database, 1, task="<script>alert(1)</script> token=secret-sentinel"
    )
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        full = await client.get("/runs")
        fragment = await client.get("/runs", headers={"HX-Request": "true"})
        assert full.status_code == fragment.status_code == 200
        assert "详情查看证据" in full.text
        assert "已记录费用，完整性未证明" in full.text
        assert "secret-sentinel" not in full.text
        assert "<script>alert(1)</script>" not in full.text
        assert "&lt;script&gt;" in full.text
        assert str(run_id) in fragment.text
        assert "<html" in full.text
        assert "<html" not in fragment.text
        assert "datetime=" in full.text
        empty = await client.get("/runs?task=absent")
        assert "清除筛选" in empty.text


async def test_native_form_empty_filters_and_dates(
    database: Path, tmp_path: Path
) -> None:
    await make_run(database, 1)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(
            "/runs?task=&model=&run_id=&workspace=&status=&from=&to=&page_size=25"
        )
        assert response.status_code == 200
        assert "共 1 个 Run" in response.text
        assert 'name="from"' in response.text
        assert 'name="to"' in response.text
        assert (await client.get("/runs?model=&model=test")).status_code == 422
