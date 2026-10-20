import html
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.projections.events import project_events, project_tools
from miniagent.dashboard.queries.events import read_events
from miniagent.dashboard.reads import ReadBudget
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import (
    EvaluationCompleted,
    NewRunEvent,
    ProviderIdentityChanged,
    RunStarted,
)
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import make_run, persist_memory
from tests.support.dashboard.events import tool_page
from tests.support.dashboard.final_review import client_for
from tests.support.dashboard.workspace_pages import publish_evaluation


async def test_provider_identity_change_hides_both_connections(
    database: Path, tmp_path: Path
) -> None:
    run_id = await make_run(database, 1)
    await SQLiteRunStore(database).append(
        NewRunEvent(
            run_id=run_id,
            payload=ProviderIdentityChanged(
                original_endpoint_identity="https://internal-old.example/private-api",
                current_endpoint_identity="https://internal-new.example/private-api",
            ),
        ),
        expected_sequence=0,
    )
    async with client_for(database, tmp_path) as client:
        response = await client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert "internal-old.example" not in response.text
    assert "internal-new.example" not in response.text
    assert "已脱敏" in response.text


@pytest.mark.parametrize(("missing",), [("output",), ("stdout_truncated",)])
async def test_unrecorded_tool_output_is_not_complete(
    database: Path, missing: str
) -> None:
    boundary = await tool_page(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        rows = connection.execute(
            "SELECT event_id,payload_json FROM run_events WHERE type='tool_end'"
        ).fetchall()
        for event_id, raw in rows:
            document = json.loads(raw)
            if missing == "output":
                del document["result"]["output"]
            else:
                document["result"]["output"] = {
                    "stderr_truncated": False,
                    "content_truncated": False,
                    "omitted_fields": [],
                }
            connection.execute(
                "UPDATE run_events SET payload_json=? WHERE event_id=?",
                (json.dumps(document), event_id),
            )
        connection.commit()
    batch = read_events(
        database, boundary, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert all(tool.completeness is None for tool in project_tools(batch.items))
    for view in project_events(batch):
        if view.type == "tool_end":
            assert isinstance(view.payload, dict)
            result = view.payload["result"]
            assert isinstance(result, dict)
            if missing != "output":
                assert isinstance(result["output"], dict)
            assert missing not in (result if missing == "output" else result["output"])


@pytest.mark.parametrize(
    ("media_type", "kind"),
    [
        ("application/json; charset=utf-8", "evaluation_report"),
        ("Application/JSON", "evaluation_report"),
        ("text/plain", "evaluation_config"),
        ("text/plain", "evaluation_summary"),
        ("text/plain", "evaluation_report"),
    ],
)
async def test_every_structured_artifact_reserves_body_budget(
    tmp_path, media_type, kind
):
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(run_id=UUID(int=1), type=kind, media_type=media_type),
        b'{"value":"' + b"x" * 10000 + b'"}',
    )
    read = ArtifactReader(tmp_path).read_content(
        UUID(int=1), ref.artifact_id, budget=ReadBudget(5, 5000, monotonic=lambda: 0)
    )
    assert read.content is None
    assert "read_limit_exceeded" in {issue.code for issue in read.issues}


async def test_large_json_integer_degrades_only_the_preview(
    database: Path, tmp_path: Path
) -> None:
    run_id = await make_run(database, 1)
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=run_id, type="evaluation_report", media_type="application/json"
        ),
        b'{"value":' + b"1" * 5000 + b"}",
    )
    async with client_for(database, tmp_path) as client:
        workspace = await client.get(f"/runs/{run_id}/workspace")
        match = re.search(r'data-boundary="([^"]+)"', workspace.text)
        assert match is not None
        token = html.unescape(match[1])
        response = await client.get(
            f"/runs/{run_id}/artifacts/{ref.artifact_id}", params={"boundary": token}
        )
    assert response.status_code == 200
    assert "json_invalid" in response.text
    assert str(ref.artifact_id) in response.text
    assert "1" * 5000 not in response.text


async def test_bad_event_time_keeps_neighbour_and_identity(
    database: Path, tmp_path: Path
) -> None:
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    for sequence in range(2):
        await store.append(
            NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=sequence
        )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET occurred_at='not-a-time' WHERE sequence=1"
        )
        connection.commit()
    async with client_for(database, tmp_path) as client:
        response = await client.get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert (
        'data-event-sequence="1"' in response.text
        and 'data-event-sequence="2"' in response.text
    )
    assert "登记时间无效" in response.text


@pytest.mark.parametrize(
    ("column",), [("created_at",), ("updated_at",), ("finished_at",)]
)
async def test_bad_run_time_is_a_row_issue(
    database: Path, tmp_path: Path, column: str
) -> None:
    run_id = await make_run(database, 1)
    other = await make_run(database, 2)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"UPDATE runs SET {column}='not-a-time' WHERE run_id=?", (str(run_id),)
        )
        connection.commit()
    async with client_for(database, tmp_path) as client:
        response = await client.get("/runs")
        detail = await client.get(f"/runs/{run_id}")
    assert response.status_code == detail.status_code == 200
    assert str(run_id) in response.text and str(other) in response.text
    assert "登记时间无效" in detail.text


async def test_corrupt_creation_time_cursor_keeps_other_runs_reachable(
    database, tmp_path
):
    identifiers = [await make_run(database, number) for number in range(1, 12)]
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET created_at='not-a-time' WHERE run_id!=?",
            (str(identifiers[0]),),
        )
        connection.commit()
    async with client_for(database, tmp_path) as client:
        first = await client.get("/runs", params={"page_size": 10})
        assert first.status_code == 200
        match = re.search(r'href="([^"]+)">下一页', first.text)
        assert match is not None
        second = await client.get(html.unescape(match[1]))
    assert second.status_code == 200
    assert str(identifiers[0]) in second.text
    assert "not-a-time" not in first.text


async def test_recent_compaction_estimates_reach_filtered_detail(
    database: Path, tmp_path: Path
) -> None:
    await persist_memory(database)
    async with client_for(database, tmp_path) as client:
        response = await client.get(
            f"/runs/{UUID(int=1)}", params={"type": "run_started"}
        )
    assert response.status_code == 200
    assert "最近压缩前 input 估计" in response.text
    assert "最近压缩后 input 估计" in response.text
    assert "Event #100" in response.text


async def test_patch_selector_rebuilds_evaluation_cards(
    database: Path, tmp_path: Path
) -> None:
    first = await publish_evaluation(database, tmp_path)
    second = await publish_evaluation(
        database,
        tmp_path,
        persist_run=False,
        attempt_id=UUID(int=201),
        submission_id=UUID(int=202),
    )
    refs = [record.reference() for record in second.values()]
    await SQLiteRunStore(database).append(
        NewRunEvent(
            run_id=UUID(int=1),
            payload=EvaluationCompleted(
                passed=True,
                result=EvaluationResult(passed=True, output="done", artifacts=refs),
            ),
            artifacts=refs,
        ),
        expected_sequence=1,
    )
    async with client_for(database, tmp_path) as client:
        initial = await client.get(f"/runs/{UUID(int=1)}/workspace")
        assert "已记录独立评测通过" not in initial.text
        match = re.search(
            r'<form action="([^"]+/workspace)".*?</form>', initial.text, re.DOTALL
        )
        assert match is not None
        form = match[0]
        params = {
            name: html.unescape(value)
            for name, value in re.findall(
                r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form
            )
        }
        params["patch"] = str(first[103].artifact_id)
        selected = await client.get(f"/runs/{UUID(int=1)}/workspace", params=params)
    assert selected.status_code == 200
    assert selected.text.count("已记录独立评测通过") == 1
    assert "必要 Artifact 内容尚未完成校验" in selected.text


async def test_conflicting_config_prevents_default_patch(
    database: Path, tmp_path: Path
) -> None:
    await publish_evaluation(database, tmp_path, config_patch_sha256="f" * 64)
    async with client_for(database, tmp_path) as client:
        response = await client.get(f"/runs/{UUID(int=1)}/workspace")
    assert response.status_code == 200
    assert "证据冲突" in response.text
    assert "评测明确关联的 patch" not in response.text
    assert " selected" not in response.text
