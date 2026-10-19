import sqlite3
from contextlib import closing
from uuid import UUID

from playwright.sync_api import Page, expect

from tests.acceptance.dashboard.conftest import BrowserSite


def test_poll_backoff_and_visibility(page: Page, site: BrowserSite) -> None:
    page.clock.install()
    page.goto(site.url + "/runs")
    expect(page.locator("#auto-refresh")).not_to_be_checked()
    original = page.locator("#run-list").inner_html()
    requests = []
    fail = [True]

    def respond(route):
        requests.append(route.request.headers.get("x-dashboard-generation"))
        if fail[0]:
            route.fulfill(
                status=503,
                body="temporary read failure",
                headers={"X-Dashboard-Generation": requests[-1]},
            )
        else:
            route.continue_()

    page.route("**/runs?*", respond)
    page.locator("#auto-refresh").check()
    delays = []
    for interval in (5000, 10000, 20000, 30000):
        before = len(requests)
        page.clock.run_for(interval)
        expect(page.locator("#refresh-status")).to_contain_text("读取失败")
        assert len(requests) == before + 1
        next_delay = page.locator("#auto-refresh").get_attribute("data-next-delay")
        assert next_delay is not None
        delays.append(int(next_delay))
        assert page.locator("#run-list").inner_html() == original
    assert delays == [10000, 20000, 30000, 30000]
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>true}); document.dispatchEvent(new Event('visibilitychange'))"
    )
    before = len(requests)
    page.clock.run_for(60000)
    assert len(requests) == before
    page.evaluate(
        "Object.defineProperty(document, 'hidden', {configurable:true, get:()=>false}); document.dispatchEvent(new Event('visibilitychange'))"
    )
    fail[0] = False
    page.clock.run_for(29999)
    assert len(requests) == before
    page.clock.run_for(1)
    expect(page.locator("#refresh-status")).to_contain_text("已更新")
    assert page.locator("#auto-refresh").get_attribute("data-next-delay") == "5000"


def test_old_response_cannot_replace_new_filter(page: Page, site: BrowserSite) -> None:
    page.goto(site.url + "/runs")
    held = []

    def hold_first(route):
        response = route.fetch()
        if not held:
            held.append((route, response))
        else:
            route.fulfill(response=response)

    page.route("**/runs?*", hold_first)
    page.locator("input[name=task]").fill("visible")
    page.locator("button[type=submit]").click()
    page.wait_for_function('document.body.dataset.generation === "1"')
    page.locator("input[name=task]").fill("second")
    page.locator("button[type=submit]").click()
    expect(page.locator("#run-list")).to_contain_text("second task")
    new_content = page.locator("#run-list").inner_html()
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_timeout(100)
    assert page.locator("#run-list").inner_html() == new_content
    assert "second" in page.url


def test_refresh_restores_far_anchor(page: Page, site: BrowserSite) -> None:
    page.goto(site.url + f"/runs/{UUID(int=1)}")
    page.get_by_text("更多事件", exact=True).click()
    expect(page.locator('[data-event-sequence="200"]')).to_be_visible()
    page.get_by_text("更多事件", exact=True).click()
    anchor = page.locator('[data-event-sequence="230"]')
    anchor.scroll_into_view_if_needed()
    identifier = anchor.get_attribute("id")
    assert identifier is not None
    anchor.locator("details").evaluate("(element)=>element.open=true")
    with closing(sqlite3.connect(site.database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_set(state_json,'$.usage.total_tokens',98765,'$.usage.input_tokens',98765)"
        )
        connection.commit()
    page.locator("#refresh").click(position={"x": 5, "y": 5}, force=True)
    expect(page.locator("#run-database")).to_contain_text("98765")
    expect(page.locator("#" + identifier)).to_be_visible()
    assert page.locator("[data-event-sequence]").count() <= 41
    assert page.locator("#" + identifier + " details").get_attribute("open") is not None


def test_workspace_preview_survives_metadata_refresh(
    page: Page, site: BrowserSite
) -> None:
    page.goto(site.url + f"/runs/{UUID(int=1)}/workspace")
    identifier = str(site.records[107].artifact_id)
    page.locator(f'#artifact-metadata a[href*="/artifacts/{identifier}"]').first.click()
    expect(page.locator("#artifact-previews")).to_contain_text("visible log")
    expect(page.locator("#artifact-previews .htmx-added")).to_have_count(0)
    original = page.locator("#artifact-previews").inner_html()
    page.locator("#refresh").click()
    expect(page.locator("#refresh-status")).to_contain_text("已更新")
    assert page.locator("#artifact-previews").inner_html() == original


def test_terminal_stops_automatic_but_manual_refresh_remains(
    page: Page, site: BrowserSite
) -> None:
    page.goto(site.url + f"/runs/{UUID(int=3)}")
    assert page.locator("#run-database").get_attribute("data-terminal") == "true"
    page.locator("#auto-refresh").click()
    expect(page.locator("#auto-refresh")).not_to_be_checked()
    page.locator("#refresh").click()
    expect(page.locator("#run-database")).to_have_attribute("data-generation", "1")
    expect(page.locator("#refresh-status")).to_contain_text("终态")
