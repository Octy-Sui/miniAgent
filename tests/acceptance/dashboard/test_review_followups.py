import sqlite3
from contextlib import closing
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from playwright.sync_api import Browser, expect

from tests.acceptance.dashboard.conftest import BrowserSite


@pytest.mark.parametrize(("javascript_enabled",), [(True,), (False,)])
def test_status_filters_survive_native_form_submission(
    site: BrowserSite, browser: Browser, javascript_enabled: bool
) -> None:
    with browser.new_context(java_script_enabled=javascript_enabled) as context:
        page = context.new_page()
        page.goto(site.url + "/runs?status=created&status=succeeded")
        statuses = page.locator('select[name="status"]')
        assert statuses.evaluate(
            "element => [...element.selectedOptions].map(option => option.value)"
        ) == ["created", "succeeded"]
        page.locator('input[name="task"]').fill("visible")
        page.get_by_role("button", name="筛选", exact=True).click()
        expect(page.locator("#run-list")).to_contain_text("共 1 个 Run")
        assert parse_qs(urlsplit(page.url).query)["status"] == ["created", "succeeded"]
        assert statuses.evaluate(
            "element => [...element.selectedOptions].map(option => option.value)"
        ) == ["created", "succeeded"]


def test_event_time_keeps_utc_source_when_displayed_in_local_timezone(
    site: BrowserSite, browser: Browser
) -> None:
    with closing(sqlite3.connect(site.database)) as connection:
        event_id, raw_time = connection.execute(
            "SELECT event_id,occurred_at FROM run_events WHERE run_id=? AND sequence=1",
            (str(UUID(int=1)),),
        ).fetchone()
    with browser.new_context(timezone_id="Asia/Shanghai", locale="en-US") as context:
        page = context.new_page()
        page.goto(site.url + f"/runs/{UUID(int=1)}")
        timestamp = page.locator(f"#event-{event_id} time")
        expect(timestamp).to_have_count(1)
        value = timestamp.get_attribute("datetime")
        assert value is not None
        assert datetime.fromisoformat(value) == datetime.fromisoformat(raw_time)
        expect(timestamp).to_have_attribute("title", value + " UTC")
        expect(timestamp).to_contain_text("GMT+8")
