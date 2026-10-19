import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import get_args
from urllib.parse import urlencode
from uuid import UUID

from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.config import LIMITS, DashboardSettings
from miniagent.dashboard.cursors import (
    CursorSigner,
    EventCursor,
    PageBoundary,
    RunListCursor,
    SourceCursor,
    filter_digest,
)
from miniagent.dashboard.parameters import EventParams, RunListParams, SnapshotParams
from miniagent.dashboard.presentation import render_page, source_href
from miniagent.dashboard.projections.compaction import (
    project_historical_memory,
    project_memory,
)
from miniagent.dashboard.projections.events import (
    project_events,
    project_tools,
    step_is_partial,
)
from miniagent.dashboard.projections.run import format_usd, project_run
from miniagent.dashboard.projections.snapshots import project_snapshot
from miniagent.dashboard.projections.spans import project_spans
from miniagent.dashboard.queries.connection import ConnectionFactory
from miniagent.dashboard.queries.events import (
    locate_event,
    read_compaction_sources,
    read_events,
    read_scope_facts,
    read_tool_facts,
)
from miniagent.dashboard.queries.runs import list_runs, read_run_page
from miniagent.dashboard.queries.snapshots import read_snapshot
from miniagent.dashboard.reads import EventBatch, ReadBudget, ReadFailure
from miniagent.dashboard.redaction import redact_text
from miniagent.dashboard.views import FieldView, MemoryView, SourceTarget
from miniagent.runtime.events import CompactionCompleted, RunEventType


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
                "cost": format_usd(item.cost_usd)
                if item.cost_usd is not None
                else "未记录",
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


def _budget(deps: PageDependencies) -> ReadBudget:
    return ReadBudget(deps.monotonic() + LIMITS.read_seconds, monotonic=deps.monotonic)


def _event_query(params: EventParams) -> list[tuple[str, str]]:
    filters = params.filters
    query: list[tuple[str, str]] = [("type", value) for value in filters.types]
    if filters.step is not None:
        query.append(("step", str(filters.step)))
    if filters.tool_name is not None:
        query.append(("tool_name", filters.tool_name))
    if filters.failures_only:
        query.append(("failures_only", "true"))
    if params.grouped:
        query.append(("grouped", "true"))
    query.append(("limit", str(params.limit)))
    return query


def _source_token(
    deps: PageDependencies, boundary: PageBoundary, params: EventParams
) -> str:
    now = deps.now()
    return deps.signer.encode(
        SourceCursor(
            boundary=boundary,
            return_filters=params.filters,
            return_grouped=params.grouped,
            issued_at=now,
            expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
        )
    )


def _field_rows(
    fields: tuple[FieldView, ...],
    deps: PageDependencies,
    boundary: PageBoundary,
    params: EventParams,
) -> list[dict[str, object]]:
    return [
        {
            "key": field.key,
            "label": field.label,
            "value": field.value,
            "source_label": field.source_label,
            "href": source_href(
                field.source,
                boundary,
                deps.signer,
                filters=params.filters,
                grouped=params.grouped,
            )
            if field.source is not None
            else None,
        }
        for field in fields
    ]


def _memory_context(
    memory: MemoryView,
    deps: PageDependencies,
    boundary: PageBoundary,
    params: EventParams,
) -> dict[str, object]:
    return {
        "version": memory.version,
        "fields": _field_rows(memory.fields, deps, boundary, params),
        "evidence": [
            {
                "claim": evidence.claim,
                "state": evidence.state,
                "source_label": evidence.source_label,
                "href": source_href(
                    evidence.source,
                    boundary,
                    deps.signer,
                    filters=params.filters,
                    grouped=params.grouped,
                )
                if evidence.source
                else None,
                "issues": evidence.issues,
            }
            for evidence in memory.evidence
        ],
        "groups": [
            {
                "first": group.first_requested_sequence,
                "cost": format_usd(group.total_cost_usd)
                if group.total_cost_usd is not None
                else "未完成校验",
                "attempts": [
                    {
                        "number": attempt.attempt,
                        "cost": format_usd(attempt.cost_usd)
                        if attempt.cost_usd is not None
                        else "未记录",
                        "fields": _field_rows(attempt.fields, deps, boundary, params),
                        "issues": attempt.issues,
                    }
                    for attempt in group.attempts
                ],
                "issues": group.issues,
            }
            for group in memory.groups
        ],
        "issues": memory.issues,
    }


def _timeline(
    deps: PageDependencies,
    batch: EventBatch,
    boundary: PageBoundary,
    params: EventParams,
    budget: ReadBudget,
) -> dict[str, object]:
    tool_keys = []
    for item in batch.items:
        if (
            item.event is not None
            and item.step_number is not None
            and item.type
            in {"tool_start", "tool_end", "tool_execution_start", "tool_execution_end"}
        ):
            call_id = getattr(item.event.payload, "tool_call_id", None)
            if isinstance(call_id, str):
                tool_keys.append((item.step_number, call_id))
    facts = (
        read_tool_facts(
            deps.settings.database,
            boundary,
            tuple(dict.fromkeys(tool_keys)),
            budget=budget,
            connect=deps.connect,
        )
        if tool_keys
        else None
    )
    scopes = tuple(
        dict.fromkeys(item.span_id for item in batch.items if item.span_id is not None)
    )
    scope_facts = (
        read_scope_facts(
            deps.settings.database,
            boundary,
            scopes,
            budget=budget,
            connect=deps.connect,
        )
        if scopes
        else None
    )
    events: list[dict[str, object]] = [
        {
            "id": str(event.event_id),
            "sequence": event.sequence,
            "type": event.type,
            "step": event.step_number,
            "partial": step_is_partial(batch.items, event.step_number)
            if event.step_number is not None
            else False,
            "summary": event.summary,
            "payload": json.dumps(event.payload, ensure_ascii=False, indent=2)
            if event.payload is not None
            else "正文未加载",
            "issues": event.issues,
            "href": source_href(
                SourceTarget(boundary.run_id, event_id=event.event_id),
                boundary,
                deps.signer,
                filters=params.filters,
                grouped=params.grouped,
            ),
        }
        for event in project_events(batch)
    ]
    tools = [
        replace(tool, tool_call_id=redact_text(tool.tool_call_id))
        for tool in project_tools(batch.items, facts=facts)
    ]
    spans = (
        [
            replace(span, span_id=redact_text(span.span_id))
            for span in project_spans(scope_facts, boundary)
        ]
        if scope_facts
        else []
    )
    next_href = None
    if batch.next_sequence is not None:
        now = deps.now()
        cursor = deps.signer.encode(
            EventCursor(
                boundary=boundary,
                filter_hash=filter_digest(params.filters),
                after_sequence=batch.next_sequence,
                issued_at=now,
                expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
            )
        )
        next_href = f"/runs/{boundary.run_id}/events?" + urlencode(
            [*_event_query(params), ("cursor", cursor)]
        )
    groups: dict[int | None, list[dict[str, object]]] = {}
    for event in events:
        step = event["step"]
        assert isinstance(step, int) or step is None
        groups.setdefault(step, []).append(event)
    return {
        "groups": groups,
        "events": events,
        "tools": tools,
        "spans": spans,
        "next_href": next_href,
        "grouped": params.grouped,
    }


def render_run(
    deps: PageDependencies, run_id: UUID, params: EventParams, *, fragment: bool = False
) -> RenderedPage:
    if params.boundary is not None or params.cursor is not None:
        raise ReadFailure(422, "main_page_boundary")
    budget = _budget(deps)
    read = read_run_page(
        deps.settings.database,
        run_id,
        params.filters,
        limit=params.limit,
        budget=budget,
        connect=deps.connect,
    )
    boundary = read.boundary
    batch = (
        locate_event(
            deps.settings.database,
            boundary,
            params.event_id,
            budget=budget,
            connect=deps.connect,
        )
        if params.event_id
        else read.events
    )
    compaction = read_compaction_sources(
        deps.settings.database,
        boundary,
        run=read.run,
        visible_events=batch.items,
        budget=budget,
        connect=deps.connect,
    )
    view = project_run(read.run, boundary, evaluation_event=read.evaluation_event)
    context: dict[str, object] = {
        "run_id": str(run_id),
        "status": view.status,
        "terminal": view.terminal,
        "as_of": boundary.as_of_sequence,
        "snapshot_version": boundary.latest_snapshot_version,
        "read_at": boundary.read_at.isoformat(),
        "boundary_token": _source_token(deps, boundary, params),
        "sections": [
            (
                "身份与结果",
                _field_rows(view.identity + view.result, deps, boundary, params),
            ),
            (
                "Usage 与预算",
                _field_rows(view.usage + view.budgets, deps, boundary, params),
            ),
        ],
        "issues": view.issues,
        "memory": _memory_context(
            project_memory(read.run, boundary, compaction), deps, boundary, params
        ),
        "timeline": _timeline(deps, batch, boundary, params, budget),
        "filters": {
            "types": tuple(params.filters.types),
            "step": params.filters.step,
            "tool_name": redact_text(params.filters.tool_name or ""),
            "failures_only": params.filters.failures_only,
            "grouped": params.grouped,
            "limit": params.limit,
        },
        "event_types": get_args(RunEventType),
        "workspace_href": f"/runs/{run_id}/workspace",
        "back_href": "/runs",
    }
    page = render_page(
        "partials/run_database.html" if fragment else "run.html", context
    )
    budget.check()
    return RenderedPage(page)


def _decode_source(deps: PageDependencies, run_id: UUID, token: str) -> SourceCursor:
    cursor = deps.signer.decode(token, purpose="source")
    if not isinstance(cursor, SourceCursor) or cursor.boundary.run_id != run_id:
        raise ReadFailure(422, "source_run_mismatch")
    return cursor


def render_events(
    deps: PageDependencies, run_id: UUID, params: EventParams, *, fragment: bool = False
) -> RenderedPage:
    budget = _budget(deps)
    if params.cursor is not None:
        cursor = deps.signer.decode(params.cursor, purpose="events")
        if (
            not isinstance(cursor, EventCursor)
            or cursor.boundary.run_id != run_id
            or cursor.filter_hash != filter_digest(params.filters)
        ):
            raise ReadFailure(422, "cursor_filters")
        boundary = cursor.boundary
        after = cursor.after_sequence
    elif params.boundary is not None:
        boundary = _decode_source(deps, run_id, params.boundary).boundary
        after = 0
    else:
        raise ReadFailure(422, "boundary_required")
    batch = (
        locate_event(
            deps.settings.database,
            boundary,
            params.event_id,
            budget=budget,
            connect=deps.connect,
        )
        if params.event_id
        else read_events(
            deps.settings.database,
            boundary,
            params.filters,
            after_sequence=after,
            limit=params.limit,
            budget=budget,
            connect=deps.connect,
        )
    )
    back_params = params
    if params.boundary is not None:
        source = _decode_source(deps, run_id, params.boundary)
        back_params = EventParams(
            filters=source.return_filters, grouped=source.return_grouped
        )
    context: dict[str, object] = {
        "title": "Event 来源 / 时间线",
        "run_id": str(run_id),
        "as_of": boundary.as_of_sequence,
        "snapshot_version": boundary.latest_snapshot_version,
        "read_at": boundary.read_at.isoformat(),
        "back_href": f"/runs/{run_id}?"
        + urlencode(_event_query(back_params))
        + (f"#event-{params.event_id}" if params.event_id else "#timeline"),
        "timeline": _timeline(deps, batch, boundary, params, budget),
        "snapshot_fields": None,
        "memory": None,
        "issues": (),
    }
    page = render_page("partials/timeline.html" if fragment else "source.html", context)
    budget.check()
    return RenderedPage(page)


def render_snapshot(
    deps: PageDependencies, run_id: UUID, version: int, params: SnapshotParams
) -> RenderedPage:
    budget = _budget(deps)
    source = _decode_source(deps, run_id, params.boundary)
    boundary = source.boundary
    read = read_snapshot(
        deps.settings.database,
        boundary,
        version,
        start=params.start,
        end=params.end,
        budget=budget,
        connect=deps.connect,
    )
    event_params = EventParams(
        filters=source.return_filters, grouped=source.return_grouped
    )
    memory = None
    stored = read.snapshot
    if (
        stored.snapshot is not None
        and stored.snapshot.state.context.current_version is not None
    ):
        facts = read_compaction_sources(
            deps.settings.database,
            boundary,
            version=stored.snapshot.state.context.current_version,
            budget=budget,
            connect=deps.connect,
        )
        if any(
            item.event is not None
            and isinstance(item.event.payload, CompactionCompleted)
            and item.event.payload.new_snapshot_id == stored.snapshot_id
            and item.event.payload.new_snapshot_version == version
            for item in facts.events
        ):
            memory = _memory_context(
                project_historical_memory(stored, boundary, facts),
                deps,
                boundary,
                event_params,
            )
    context: dict[str, object] = {
        "title": f"Snapshot v{version} · 消息 [{read.start}, {read.end}) / {read.message_count if read.message_count is not None else '未加载'}",
        "run_id": str(run_id),
        "as_of": boundary.as_of_sequence,
        "snapshot_version": boundary.latest_snapshot_version,
        "read_at": boundary.read_at.isoformat(),
        "back_href": f"/runs/{run_id}?"
        + urlencode(_event_query(event_params))
        + "#memory",
        "snapshot_fields": _field_rows(
            project_snapshot(read), deps, boundary, event_params
        ),
        "timeline": None,
        "memory": memory,
        "issues": stored.issues,
    }
    page = render_page("source.html", context)
    budget.check()
    return RenderedPage(page)
