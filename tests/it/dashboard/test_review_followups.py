import html
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.support.dashboard.detail_pages import populated
from tests.support.dashboard.final_review import client_for
from tests.support.dashboard.workspace_pages import publish_evaluation


@pytest.mark.parametrize(("unreadable",), [("manifest",), ("content",)])
async def test_unreadable_artifact_does_not_hide_other_workspace_evidence(
    database: Path, tmp_path: Path, unreadable: str
) -> None:
    if os.geteuid() == 0:
        pytest.skip("requires an unprivileged user to exercise file permissions")
    root = tmp_path / "artifacts"
    records = await publish_evaluation(database, root)
    record = records[104 if unreadable == "manifest" else 106]
    path = (
        root / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
        if unreadable == "manifest"
        else root / "content" / record.sha256[:2] / record.sha256
    )
    mode = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0)
    try:
        with pytest.raises(PermissionError):
            path.read_bytes()
        async with client_for(database, root) as client:
            workspace = await client.get(f"/runs/{record.run_id}/workspace")
            assert workspace.status_code == 200
            assert str(records[107].artifact_id) in workspace.text
            match = re.search(r'data-boundary="([^"]+)"', workspace.text)
            assert match is not None
            params = {"boundary": html.unescape(match[1])}
            denied = await client.get(
                f"/runs/{record.run_id}/artifacts/{record.artifact_id}", params=params
            )
            assert denied.status_code == 409
            other = await client.get(
                f"/runs/{record.run_id}/artifacts/{records[107].artifact_id}",
                params=params,
            )
            assert other.status_code == 200
            assert "visible recorded log" in other.text
    finally:
        path.chmod(mode)


@pytest.mark.parametrize(("filtered",), [(False,), (True,)])
async def test_source_return_locates_event_outside_initial_batch(
    database: Path, tmp_path: Path, filtered: bool
) -> None:
    run_id, _, events = await populated(database)
    target = events[180].event_id
    async with client_for(database, tmp_path) as client:
        first = await client.get(
            f"/runs/{run_id}",
            params={"type": "model_requested", "grouped": "true"} if filtered else {},
        )
        match = re.search(r'data-boundary="([^"]+)"', first.text)
        assert match is not None
        source = await client.get(
            f"/runs/{run_id}/events",
            params={"boundary": html.unescape(match[1]), "event_id": str(target)},
        )
        match = re.search(r'href="([^"]+)">返回 Run 详情和原筛选', source.text)
        assert match is not None
        back_href = html.unescape(match[1])
        returned = await client.get(back_href)
    assert returned.status_code == 200
    assert f'id="event-{target}"' in returned.text
    assert returned.text.count("data-event-sequence=") <= 41
    query = parse_qs(urlsplit(back_href).query)
    assert query["event_id"] == [str(target)]
    if filtered:
        assert query["type"] == ["model_requested"]
        assert query["grouped"] == ["true"]


async def test_timeline_and_source_expose_recorded_event_time(
    database: Path, tmp_path: Path
) -> None:
    run_id, _, events = await populated(database)
    target = events[0].event_id
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET occurred_at='2026-10-14T01:02:03.456789+00:00' WHERE event_id=?",
            (str(target),),
        )
        connection.commit()
    async with client_for(database, tmp_path) as client:
        detail = await client.get(f"/runs/{run_id}")
        match = re.search(r'data-boundary="([^"]+)"', detail.text)
        assert match is not None
        source = await client.get(
            f"/runs/{run_id}/events",
            params={"boundary": html.unescape(match[1]), "event_id": str(target)},
        )
    for response in (detail, source):
        assert response.status_code == 200
        article = re.search(
            rf'<article id="event-{target}".*?</article>', response.text, re.DOTALL
        )
        assert article is not None
        assert 'datetime="2026-10-14T01:02:03.456789+00:00"' in article[0]
        assert "2026-10-14T01:02:03.456789+00:00 UTC" in article[0]
