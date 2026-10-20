import html
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from miniagent.runtime.events import NewRunEvent, RunStarted
from tests.support.dashboard.builders import make_run
from tests.support.dashboard.detail_pages import populated


async def test_detail_refresh_changes_all_database_facts(
    database: Path, tmp_path: Path
) -> None:
    run_id, store, _ = await populated(database)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        first = await client.get(f"/runs/{run_id}")
        assert first.status_code == 200
        assert 'data-as-of="240"' in first.text
        assert 'data-snapshot-version="2"' in first.text
        await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=240
        )
        refreshed = await client.get(f"/runs/{run_id}", headers={"HX-Request": "true"})
        assert 'data-as-of="241"' in refreshed.text
        assert 'id="run-database"' in refreshed.text
        assert "<html" not in refreshed.text
        assert (await client.head(f"/runs/{run_id}")).content == b""


async def test_event_pagination_keeps_original_overview(
    database: Path, tmp_path: Path
) -> None:
    run_id, store, _ = await populated(database)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        first = await client.get(f"/runs/{run_id}")
        match = re.search(r'href="([^"]+)"[^>]*>更多事件', first.text)
        assert match is not None
        link = html.unescape(match[1])
        await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=240
        )
        fragment = await client.get(link, headers={"HX-Request": "true"})
        assert fragment.status_code == 200
        assert 'data-event-sequence="241"' not in fragment.text
        assert 'id="run-database"' not in fragment.text
        normal = await client.get(link)
        assert "<html" in normal.text
        assert f'href="/runs/{run_id}' in normal.text
        assert (await client.get(f"/runs/{run_id}?cursor=invalid")).status_code == 422


async def test_source_links_keep_boundary_and_filters(
    database: Path, tmp_path: Path
) -> None:
    run_id, _, events = await populated(database)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(f"/runs/{run_id}?type=run_started&grouped=true")
        match = re.search(r'data-boundary="([^"]+)"', response.text)
        assert match is not None
        token = html.unescape(match[1])
        source = await client.get(
            f"/runs/{run_id}/snapshots/2",
            params={"boundary": token, "start": 0, "end": 1},
        )
        assert source.status_code == 200
        assert "requested-message" in source.text
        assert "unrequested-message" not in source.text
        assert "grouped=true" in html.unescape(source.text)
        assert "type=run_started" in html.unescape(source.text)
        event = await client.get(
            f"/runs/{run_id}/events",
            params={
                "boundary": token,
                "event_id": str(events[230].event_id),
                "type": "model_requested",
            },
        )
        assert event.status_code == 200
        assert str(events[230].event_id) in event.text
        assert (
            await client.get(f"/runs/{run_id}/snapshots/3", params={"boundary": token})
        ).status_code == 422


async def test_detail_native_filter_form(database: Path, tmp_path: Path) -> None:
    run_id = await make_run(database, 1)
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(f"/runs/{run_id}?type=&step=&tool_name=")
        assert response.status_code == 200
        assert 'name="type" multiple' in response.text
        assert "尚未生成持久化 Task Memory" in response.text


@pytest.mark.parametrize("missing", ["content", "tool_calls"])
async def test_snapshot_projection_keeps_absent_message_fields_absent(
    database: Path,
    missing: str,
) -> None:
    from miniagent.dashboard.parameters import EventFilters
    from miniagent.dashboard.projections.snapshots import project_snapshot
    from miniagent.dashboard.queries.runs import read_run_page
    from miniagent.dashboard.queries.snapshots import read_snapshot
    from miniagent.dashboard.reads import ReadBudget

    run_id, _, _ = await populated(database)
    with closing(sqlite3.connect(database)) as connection:
        raw = json.loads(
            connection.execute(
                "SELECT snapshot_json FROM run_snapshots WHERE version=2"
            ).fetchone()[0]
        )
        message = {
            "role": "assistant",
            "content": "requested-message",
            "tool_calls": [
                {"id": "call1", "name": "read_file", "arguments": {"path": "a"}}
            ],
        }
        del message[missing]
        raw["state"]["conversation"]["messages"][0] = message
        connection.execute(
            "INSERT INTO run_snapshots SELECT run_id,3,'00000000-0000-0000-0000-000000009999',event_sequence,schema_version,?,created_at FROM run_snapshots WHERE version=2",
            (json.dumps(raw),),
        )
        connection.execute("UPDATE runs SET latest_snapshot_version=3")
        connection.commit()
    boundary = read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    ).boundary
    source = read_snapshot(
        database, boundary, 3, start=0, end=1, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert source.snapshot.snapshot is not None
    projected = project_snapshot(source)
    assert json.loads(projected[0].value).get(missing) is None
    assert missing not in json.loads(projected[0].value)
    assert "assistant" in projected[0].value


async def test_source_tokens_do_not_store_raw_search_terms(
    database: Path, tmp_path: Path
) -> None:
    import base64
    from urllib.parse import parse_qs, urlsplit

    run_id, _, _ = await populated(database)
    secret = "sk-search-secret-1234567890123456"
    app = create_app(DashboardSettings(database=database, artifacts=tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        page = await client.get(
            f"/runs/{run_id}", params={"tool_name": secret, "grouped": "true"}
        )
        assert page.status_code == 200
        token = re.search(r'data-boundary="([^"]+)"', page.text)
        assert token is not None
        payload = token[1].split(".")[0]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        assert secret not in decoded
        # This fixture has no compaction source link; verify the public presenter explicitly.
        from miniagent.dashboard.cursors import SourceCursor
        from miniagent.dashboard.parameters import EventFilters
        from miniagent.dashboard.presentation import source_href
        from miniagent.dashboard.views import SourceTarget

        cursor = app.state.signer.decode(html.unescape(token[1]), purpose="source")
        assert isinstance(cursor, SourceCursor)
        href = source_href(
            SourceTarget(run_id, snapshot_version=2, start=0, end=1),
            cursor.boundary,
            app.state.signer,
            filters=EventFilters(tool_name=secret),
            grouped=True,
        )
        response = await client.get(href)
        assert response.status_code == 200
        back = re.search(r'href="([^\"]+)"[^>]*>返回 Run 详情和原筛选', response.text)
        assert back is not None
        assert parse_qs(urlsplit(html.unescape(back[1])).query)["tool_name"] == [secret]
