from datetime import timedelta
from urllib.parse import urlencode

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import CursorSigner, PageBoundary, SourceCursor
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.reads import ReadFailure
from miniagent.dashboard.views import SourceTarget

_ENV = Environment(
    loader=PackageLoader("miniagent.dashboard", "templates"),
    autoescape=select_autoescape(("html",)),
    undefined=StrictUndefined,
)


def render_page(template: str, context: dict[str, object]) -> str:
    return _ENV.get_template(template).render(**context)


def source_href(
    target: SourceTarget,
    boundary: PageBoundary,
    signer: CursorSigner,
    *,
    filters: EventFilters | None = None,
    grouped: bool = False,
) -> str:
    if target.run_id != boundary.run_id:
        raise ReadFailure(422, "source_run_mismatch")
    selected_filters = filters or EventFilters()
    now = signer.now()
    token = signer.encode(
        SourceCursor(
            boundary=boundary,
            return_filters=selected_filters.model_copy(update={"tool_name": None}),
            return_grouped=grouped,
            issued_at=now,
            expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
        )
    )
    base = f"/runs/{target.run_id}"
    query: dict[str, str | int] = {"boundary": token}
    if selected_filters.tool_name is not None:
        query["tool_name"] = selected_filters.tool_name
    if target.event_id is not None:
        query["event_id"] = str(target.event_id)
        return base + "/events?" + urlencode(query) + f"#event-{target.event_id}"
    if target.snapshot_version is not None:
        if target.start is not None:
            query["start"] = target.start
        if target.end is not None:
            query["end"] = target.end
        return base + f"/snapshots/{target.snapshot_version}?" + urlencode(query)
    return base + "?" + urlencode(query)
