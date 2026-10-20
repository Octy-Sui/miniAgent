import asyncio
import base64
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from playwright.sync_api import Browser, expect

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import (
    EvaluationCompleted,
    NewRunEvent,
    StepCompleted,
    StepStarted,
    ToolEnded,
    ToolStarted,
)
from miniagent.storage import SQLiteRunStore
from miniagent.storage.codecs import encode_datetime
from miniagent.tools.contracts import ToolOutputInfo, ToolResult, ToolReturnValidation
from tests.support.dashboard.builders import (
    evaluation_example,
    make_database,
    persist_memory,
)
from tests.support.dashboard.fixtures_browser import BrowserSite, serve_site
from tests.support.dashboard.observation import capture_read_only
from tests.support.dashboard.workspace_pages import publish_evaluation

RUN_ID = UUID(int=1)


async def seed_journey(database: Path, artifacts: Path):
    await make_database(database)
    await persist_memory(database)
    records = await publish_evaluation(
        database, artifacts, persist_run=False, long_log=True
    )
    store = SQLiteRunStore(database)
    event = None
    payloads = [
        StepStarted(),
        ToolStarted(
            call_index=0, tool_call_id="journey-call", tool_name="shell", arguments={}
        ),
        ToolEnded(
            call_index=0,
            tool_call_id="journey-call",
            tool_name="shell",
            status="success",
            result=ToolResult(
                tool_call_id="journey-call",
                status="success",
                content="visible tool output",
                data={"exit_code": 0},
                output=ToolOutputInfo(stdout_truncated=True),
            ),
            raw_result=None,
            return_validation=ToolReturnValidation(
                disposition="not_returned", reason="fixture_not_returned"
            ),
            observation=None,
            observation_missing_reason="fixture_not_observed",
        ),
        StepCompleted(failed=False),
    ]
    for n, payload in enumerate(payloads, 101):
        item = await store.append(
            NewRunEvent(
                run_id=RUN_ID,
                payload=payload,
                step_number=4,
                span_id="0000000000000042" if n in (102, 103) else "0000000000000004",
                parent_span_id="0000000000000004" if n in (102, 103) else None,
            ),
            expected_sequence=n - 1,
        )
        if n == 103:
            event = item
    refs = [record.reference() for record in records.values()]
    await store.append(
        NewRunEvent(
            run_id=RUN_ID,
            payload=EvaluationCompleted(
                passed=True,
                result=EvaluationResult(passed=True, output="done", artifacts=refs),
            ),
            artifacts=refs,
        ),
        expected_sequence=104,
    )
    current = await store.get(RUN_ID)
    terminal = evaluation_example(artifacts).run.record
    assert terminal is not None and event is not None
    state = current.state.model_copy(
        update={
            "lifecycle": terminal.state.lifecycle,
            "execution": terminal.state.execution.model_copy(
                update={"steps_started": 4}
            ),
        }
    )
    state = type(state).model_validate(state.model_dump())
    assert terminal.recovery_context is not None
    assert state.lifecycle.finished_at is not None
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=?,recovery_context_json=?,status=?,updated_at=?,finished_at=?",
            (
                state.model_dump_json(),
                terminal.recovery_context.model_dump_json(),
                state.lifecycle.status,
                encode_datetime(state.lifecycle.updated_at),
                encode_datetime(state.lifecycle.finished_at),
            ),
        )
        connection.commit()
    records["tool_event"] = event.event_id
    long_path = "a_" + "long_name_" * 40 + ".py"
    diff = f"diff --git a/{long_path} b/{long_path}\n--- a/{long_path}\n+++ b/{long_path}\n@@ -1 +1 @@\n-old\n+long visible diff\n"
    records["long_patch"] = await FileArtifactStore(artifacts).put(
        ArtifactWrite(
            run_id=RUN_ID,
            type="workspace_patch",
            media_type="text/x-diff",
            metadata={"files": [long_path]},
        ),
        diff.encode(),
    )
    return records


@pytest.fixture
def journey_site(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    artifacts = tmp_path / "artifacts"
    records = asyncio.run(seed_journey(database, artifacts))
    with serve_site(database, artifacts, records) as running:
        yield running


def token_boundary(token: str | None) -> dict:
    assert token is not None
    payload = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))[
        "boundary"
    ]


def test_persisted_evidence_journey(
    journey_site: BrowserSite, browser: Browser
) -> None:
    site = journey_site
    before = capture_read_only(site.database, site.artifacts)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    external = []
    page.on(
        "request",
        lambda request: (
            external.append(request.url)
            if urlsplit(request.url).netloc != urlsplit(site.url).netloc
            else None
        ),
    )
    try:
        page.goto(site.url + "/")
        expect(page).to_have_url(site.url + "/runs")
        page.locator(f'a[href="/runs/{RUN_ID}"]').click()
        detail_boundary = token_boundary(
            page.locator("#run-database").get_attribute("data-boundary")
        )
        assert detail_boundary["run_id"] == str(RUN_ID)
        expect(page.locator("#memory")).to_contain_text("goal 2")
        page.locator("input[name=grouped]").check()
        page.locator("#event-filters button").click()
        expect(page.locator("#run-database")).to_have_attribute("data-generation", "1")
        expect(page.locator("#timeline")).to_contain_text("completeness=truncated")
        expect(page.locator("#timeline")).to_contain_text("status=success")
        tool = page.locator(f"#event-{site.records['tool_event']}")
        tool.locator("summary").click()
        expect(tool.locator("pre")).to_contain_text("visible tool output")
        detail_boundary = token_boundary(
            page.locator("#run-database").get_attribute("data-boundary")
        )
        tool.get_by_role("link", name="定位来源").click()
        source_token = parse_qs(urlsplit(page.url).query)["boundary"][0]
        assert token_boundary(source_token) == detail_boundary
        expect(page.locator(f"#event-{site.records['tool_event']}")).to_be_visible()
        page.get_by_role("link", name="返回 Run 详情和原筛选").click()
        snapshot_link = page.locator('#memory a[href*="/snapshots/4?"]').first
        boundary_before = token_boundary(
            page.locator("#run-database").get_attribute("data-boundary")
        )
        snapshot_link.click()
        assert (
            token_boundary(parse_qs(urlsplit(page.url).query)["boundary"][0])
            == boundary_before
        )
        expect(
            page.get_by_role("heading", name="Snapshot v4", exact=False)
        ).to_be_visible()
        expect(page.locator("main")).to_contain_text("goal 2")
        page.get_by_role("link", name="返回 Run 详情和原筛选").click()
        detail_read_at = page.locator("#run-database").get_attribute("data-read-at")
        page.get_by_role("link", name="Workspace", exact=True).click()
        expect(page.locator("#workspace-facts")).to_contain_text("已记录独立评测通过")
        expect(page.locator("#workspace-facts")).to_contain_text(
            "目标测试清单未持久化，无法复核完整覆盖"
        )
        assert (
            page.locator("#workspace-facts").get_attribute("data-read-at")
            != detail_read_at
        )
        expect(
            page.get_by_text("目标测试清单未持久化，无法复核完整覆盖", exact=True)
        ).to_be_in_viewport()
        log_id = site.records[107].artifact_id
        page.locator(f'#artifact-metadata a[href*="/artifacts/{log_id}"]').click()
        expect(page.locator("#artifact-previews")).to_contain_text("脱敏后偏移 0")
        page.locator("#artifact-previews").get_by_role("link", name="继续预览").click()
        expect(page.locator("#artifact-previews")).not_to_contain_text("脱敏后偏移 0")
        page.get_by_role("link", name="Runs", exact=True).first.click()
        expect(page).to_have_url(site.url + "/runs")
        assert not external
        assert capture_read_only(site.database, site.artifacts) == before
    finally:
        context.close()


def test_without_javascript_has_get_navigation(
    journey_site: BrowserSite, browser: Browser
) -> None:
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(journey_site.url + f"/runs/{RUN_ID}?limit=2")
        expect(page.get_by_text("使用浏览器重新加载刷新", exact=False)).to_be_visible()
        assert page.locator("time").first.inner_text().endswith("UTC")
        page.get_by_role("link", name="更多事件").click()
        expect(page.get_by_role("link", name="返回 Run 详情和原筛选")).to_be_visible()
        page.goto(journey_site.url + f"/runs/{RUN_ID}")
        page.locator('#memory a[href*="/snapshots/4?"]').first.click()
        expect(
            page.get_by_role("heading", name="Snapshot v4", exact=False)
        ).to_be_visible()
        page.goto(journey_site.url + f"/runs/{RUN_ID}/workspace")
        identifier = journey_site.records[107].artifact_id
        page.locator(f'#artifact-metadata a[href*="/artifacts/{identifier}"]').click()
        expect(page.locator("#artifact-preview-content")).to_contain_text(
            "脱敏后偏移 0"
        )
        page.get_by_role("link", name="继续预览").click()
        expect(page.locator("#artifact-preview-content")).not_to_contain_text(
            "脱敏后偏移 0"
        )
    finally:
        context.close()


def test_broken_source_preserves_other_evidence(
    journey_site: BrowserSite, browser: Browser
) -> None:
    root = journey_site.artifacts
    record = journey_site.records[106]
    target = root / "content" / record.sha256[:2] / record.sha256
    target.write_bytes(b"corrupt report")
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(journey_site.url + f"/runs/{RUN_ID}/workspace")
        expect(page.locator("#workspace-facts")).not_to_contain_text(
            "已记录独立评测通过"
        )
        expect(page.locator("#workspace-facts")).to_contain_text("证据冲突")
        identifier = journey_site.records[107].artifact_id
        page.locator(f'#artifact-metadata a[href*="/artifacts/{identifier}"]').click()
        expect(page.locator("#artifact-previews")).to_contain_text("visible log")
    finally:
        context.close()


def test_status_text_has_distinct_color_cues(
    site: BrowserSite, browser: Browser
) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(site.url + "/runs")
        active = (
            page.locator("tr")
            .filter(has=page.locator(f'a[href="/runs/{UUID(int=1)}"]'))
            .locator(".badge")
        )
        done = (
            page.locator("tr")
            .filter(has=page.locator(f'a[href="/runs/{UUID(int=3)}"]'))
            .locator(".badge")
        )
        assert active.inner_text() == "created"
        assert done.inner_text() == "succeeded"
        assert active.evaluate(
            "(element)=>getComputedStyle(element).color"
        ) != done.evaluate("(element)=>getComputedStyle(element).color")
    finally:
        context.close()


def test_long_patch_paths_wrap_on_narrow_viewport(
    journey_site: BrowserSite, browser: Browser
) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        page.goto(journey_site.url + f"/runs/{RUN_ID}")
        token = page.locator("#run-database").get_attribute("data-boundary")
        identifier = journey_site.records["long_patch"].artifact_id
        page.goto(
            journey_site.url
            + f"/runs/{RUN_ID}/workspace?boundary={token}&patch={identifier}"
        )
        expect(page.locator("#patch-preview")).to_contain_text("long visible diff")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.goto(journey_site.url + f"/runs/{RUN_ID}/workspace")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        context.close()
