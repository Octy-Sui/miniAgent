from uuid import UUID

from playwright.sync_api import Page, expect

from tests.acceptance.dashboard.conftest import BrowserSite


def test_content_never_executes_and_assets_are_local(
    page: Page, site: BrowserSite
) -> None:
    sentinel = '<script>window.dashboardSentinel=1</script><img src="https://evil.invalid/pixel" onerror="window.dashboardSentinel=2">'
    ref = site.records["sentinel"]
    external = []
    errors = []
    page.on(
        "request",
        lambda request: (
            external.append(request.url)
            if not request.url.startswith(site.url)
            else None
        ),
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(site.url + f"/runs/{UUID(int=1)}/workspace")
    page.locator(f'#artifact-metadata a[href*="/artifacts/{ref.artifact_id}"]').click()
    expect(page.locator("#artifact-previews")).to_contain_text(sentinel)
    assert page.evaluate("window.dashboardSentinel") is None
    assert page.evaluate("localStorage.length") == 0
    assert not external
    assert not errors
    assert page.evaluate("htmx.config.allowEval") is False
    assert page.evaluate("htmx.config.allowScriptTags") is False
    assert page.evaluate("htmx.config.includeIndicatorStyles") is False
    assert page.evaluate("htmx.config.historyCacheSize") == 0
