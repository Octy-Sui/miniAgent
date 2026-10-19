from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.config import LIMITS, DashboardSettings
from miniagent.dashboard.cursors import CursorSigner, RunListCursor, filter_digest
from miniagent.dashboard.parameters import RunListParams
from miniagent.dashboard.presentation import render_page
from miniagent.dashboard.queries.connection import ConnectionFactory
from miniagent.dashboard.queries.runs import list_runs
from miniagent.dashboard.reads import ReadBudget, ReadFailure
from miniagent.dashboard.redaction import redact_text


@dataclass(frozen=True)
class PageDependencies:
    settings: DashboardSettings
    signer: CursorSigner
    reader: ArtifactReader
    connect: ConnectionFactory
    now: Callable[[], datetime]
    monotonic: Callable[[], float]


@dataclass(frozen=True)
class RenderedPage:
    html: str
    status_code: int = 200


def render_runs(
    deps: PageDependencies, params: RunListParams, *, fragment: bool = False
) -> RenderedPage:
    budget = ReadBudget(
        deps.monotonic() + LIMITS.read_seconds, monotonic=deps.monotonic
    )
    position = None
    digest = filter_digest(params.filters)
    if params.cursor is not None:
        cursor = deps.signer.decode(params.cursor, purpose="runs")
        if not isinstance(cursor, RunListCursor) or cursor.filter_hash != digest:
            raise ReadFailure(422, "cursor_filters")
        position = cursor.position
    result = list_runs(
        deps.settings.database,
        params.filters,
        page_size=params.page_size,
        after=position,
        budget=budget,
        connect=deps.connect,
    )
    rows = []
    for item in result.items:
        budget.check()
        rows.append(
            {
                "id": str(item.envelope.run_id),
                "short_id": str(item.envelope.run_id)[:8],
                "task": redact_text(item.task)[:200]
                if item.task is not None
                else "未加载",
                "model": redact_text(item.model)
                if item.model is not None
                else "未记录",
                "workspace": item.workspace_kind,
                "status": item.envelope.status,
                "reason": redact_text(item.reason)
                if item.reason is not None
                else "未记录",
                "verification": "详情查看证据",
                "duration": f"{item.active_duration_ms} ms"
                if item.active_duration_ms is not None
                else "未记录",
                "cost": str(item.cost_usd) if item.cost_usd is not None else "未记录",
                "created": item.envelope.created_at.isoformat(),
                "href": f"/runs/{item.envelope.run_id}",
            }
        )
    query = [
        (key, str(value))
        for key, value in params.filters.model_dump(mode="json").items()
        if value is not None and key != "statuses"
    ]
    query = [(key, value) for key, value in query if value != "[]"]
    # Public GET names differ from the normalized filter model.
    query = [
        ({"created_from": "from", "created_to": "to"}.get(key, key), value)
        for key, value in query
    ]
    query.extend(("status", status) for status in params.filters.statuses)
    query.append(("page_size", str(params.page_size)))
    next_href = None
    if result.next_position is not None:
        now = deps.now()
        token = deps.signer.encode(
            RunListCursor(
                filter_hash=digest,
                position=result.next_position,
                issued_at=now,
                expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
            )
        )
        next_href = "/runs?" + urlencode([*query, ("cursor", token)])
    html = render_page(
        "partials/run_list.html" if fragment else "runs.html",
        {
            "rows": rows,
            "total": result.total,
            "counts": result.counts,
            "next_href": next_href,
            "filters": {
                key: redact_text(str(value))
                for key, value in params.filters.model_dump(mode="json").items()
                if value is not None
            },
            "page_size": params.page_size,
        },
    )
    budget.check()
    return RenderedPage(html)
