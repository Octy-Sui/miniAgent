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
    ManifestCursor,
    PageBoundary,
    PreviewCursor,
    RunListCursor,
    SourceCursor,
    filter_digest,
)
from miniagent.dashboard.evaluation_evidence import adapt_evaluation
from miniagent.dashboard.parameters import (
    ArtifactParams,
    EventFilters,
    EventParams,
    RunListParams,
    SnapshotParams,
    WorkspaceParams,
)
from miniagent.dashboard.presentation import render_page, source_href
from miniagent.dashboard.previews import project_preview
from miniagent.dashboard.projections.compaction import (
    project_historical_memory,
    project_memory,
)
from miniagent.dashboard.projections.evaluation import project_evaluations
from miniagent.dashboard.projections.events import (
    project_events,
    project_tools,
    step_is_partial,
)
from miniagent.dashboard.projections.run import format_usd, project_run
from miniagent.dashboard.projections.snapshots import project_snapshot
from miniagent.dashboard.projections.spans import project_spans
from miniagent.dashboard.projections.workspace import choose_patch, parse_diff
from miniagent.dashboard.queries.connection import ConnectionFactory
from miniagent.dashboard.queries.events import (
    locate_event,
    read_compaction_sources,
    read_events,
    read_scope_facts,
    read_tool_facts,
)
from miniagent.dashboard.queries.runs import (
    list_runs,
    read_run_page,
    read_workspace_page,
)
from miniagent.dashboard.queries.snapshots import read_snapshot
from miniagent.dashboard.reads import (
    EventBatch,
    ManifestItem,
    ManifestPage,
    ReadBudget,
    ReadFailure,
    ReadIssue,
    VerifiedContent,
)
from miniagent.dashboard.redaction import redact_json, redact_text
from miniagent.dashboard.views import FieldView, MemoryView, SourceTarget
from miniagent.runtime.events import (
    CompactionCompleted,
    EvaluationCompleted,
    RunEventType,
)


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
                "created": item.envelope.created_at.isoformat()
                if item.envelope.created_at
                else None,
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
            "read_at": result.read_at.isoformat(),
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
            return_filters=params.filters.model_copy(update={"tool_name": None}),
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
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
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
    completed = [
        item
        for item in compaction.events
        if item.event is not None
        and isinstance(item.event.payload, CompactionCompleted)
        and item.run_id == run_id
        and item.sequence <= boundary.as_of_sequence
    ]
    latest = max(completed, key=lambda item: item.sequence, default=None)
    latest_payload = (
        latest.event.payload
        if latest is not None and latest.event is not None
        else None
    )
    view = project_run(
        read.run,
        boundary,
        evaluation_event=read.evaluation_event,
        latest_compaction=latest_payload
        if isinstance(latest_payload, CompactionCompleted)
        else None,
    )
    if latest is not None:
        view = replace(
            view,
            usage=tuple(
                replace(
                    field,
                    source_label=f"Event #{latest.sequence}",
                    source=SourceTarget(run_id, event_id=latest.event_id),
                )
                if field.key in {"old_input_tokens", "new_input_tokens"}
                else field
                for field in view.usage
            ),
        )
    context: dict[str, object] = {
        "run_id": str(run_id),
        "located": params.event_id is not None,
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
            filters=source.return_filters.model_copy(
                update={"tool_name": params.filters.tool_name}
            ),
            grouped=source.return_grouped,
        )
    context: dict[str, object] = {
        "title": "Event 来源 / 时间线",
        "run_id": str(run_id),
        "as_of": boundary.as_of_sequence,
        "snapshot_version": boundary.latest_snapshot_version,
        "read_at": boundary.read_at.isoformat(),
        "back_href": f"/runs/{run_id}?"
        + urlencode(
            _event_query(back_params)
            + ([("event_id", str(params.event_id))] if params.event_id else [])
        )
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
        filters=source.return_filters.model_copy(
            update={"tool_name": params.tool_name}
        ),
        grouped=source.return_grouped,
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


def _artifact_href(run_id: UUID, artifact_id: UUID, token: str) -> str:
    return f"/runs/{run_id}/artifacts/{artifact_id}?" + urlencode({"boundary": token})


def _metadata(
    deps: PageDependencies,
    run_id: UUID,
    boundary: PageBoundary,
    token: str,
    budget: ReadBudget,
    *,
    after: UUID | None = None,
) -> dict[str, object]:
    page = deps.reader.list_manifests(run_id, after_id=after, budget=budget)
    items = []
    for item in page.items:
        record = item.record
        items.append(
            {
                "id": str(item.artifact_id),
                "type": redact_text(record.type) if record else "未解析",
                "metadata": json.dumps(
                    redact_json(record.metadata, schema="metadata"),
                    ensure_ascii=False,
                    indent=2,
                )
                if record
                else "未加载",
                "sha256": record.sha256 if record else "未加载",
                "size": record.size_bytes if record else None,
                "issues": item.issues,
                "href": _artifact_href(run_id, item.artifact_id, token),
            }
        )
    next_href = None
    if page.next_id is not None:
        now = deps.now()
        cursor = deps.signer.encode(
            ManifestCursor(
                boundary=boundary,
                after_id=page.next_id,
                issued_at=now,
                expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
            )
        )
        next_href = f"/runs/{run_id}/workspace?" + urlencode(
            {"boundary": token, "cursor": cursor}
        )
    return {
        "items": items,
        "next_href": next_href,
        "issues": page.issues,
        "complete": page.complete,
        "read_at": page.read_at.isoformat(),
        "page": page,
    }


def _read_evidence_content(
    deps: PageDependencies, run_id: UUID, item: ManifestItem, budget: ReadBudget
) -> VerifiedContent | None:
    if item.record is None:
        return None
    try:
        return deps.reader.read_content(run_id, item.artifact_id, budget=budget)
    except ReadFailure as exc:
        if exc.status_code not in (404, 409):
            raise
        return VerifiedContent(
            item.record,
            None,
            deps.now(),
            (ReadIssue(exc.code, "登记证据无法通过当前内容校验", item.artifact_id),),
        )


def _patch_context(
    deps: PageDependencies,
    run_id: UUID,
    identifier: UUID | None,
    token: str,
    boundary: PageBoundary,
    budget: ReadBudget,
    contents: dict[UUID, VerifiedContent],
) -> dict[str, object] | None:
    if identifier is None:
        return None
    content = contents.get(identifier)
    if content is None:
        item = deps.reader.read_manifest(run_id, identifier, budget=budget)
        if item.record is None or item.record.type != "workspace_patch":
            raise ReadFailure(422, "patch_selection_invalid")
        content = _read_evidence_content(deps, run_id, item, budget)
        assert content is not None
        contents[identifier] = content
    preview = project_preview(content, budget=budget)
    parsed = None
    if content.content is not None:
        try:
            text = content.content.decode("utf-8")
        except UnicodeError:
            text = None
        if text is not None:
            recorded_files = content.record.metadata.get("files")
            parsed = parse_diff(
                text,
                budget=budget,
                recorded_files=tuple(recorded_files)
                if isinstance(recorded_files, list)
                and all(isinstance(path, str) for path in recorded_files)
                else None,
            )
    # Structured file paths are small labels; text is bounded by the same preview mechanism.
    files = (
        [
            {"old": file.old_path, "new": file.new_path, "kind": file.kind}
            for file in parsed.files
        ]
        if parsed
        else []
    )
    return {
        "id": str(identifier),
        "files": files,
        "complete": parsed.complete if parsed else False,
        "issues": (*content.issues, *(parsed.issues if parsed else ())),
        "preview": preview.text,
        "recorded_files": json.dumps(
            redact_json(content.record.metadata.get("files")), ensure_ascii=False
        ),
        "href": _artifact_href(run_id, identifier, token),
    }


def render_workspace(
    deps: PageDependencies,
    run_id: UUID,
    params: WorkspaceParams,
    *,
    fragment: bool = False,
) -> RenderedPage:
    budget = _budget(deps)
    if params.boundary is not None:
        source = _decode_source(deps, run_id, params.boundary)
        boundary = source.boundary
        if params.cursor is not None and params.patch is None:
            cursor = deps.signer.decode(params.cursor, purpose="manifests")
            if not isinstance(cursor, ManifestCursor) or cursor.boundary != boundary:
                raise ReadFailure(422, "manifest_cursor_binding")
            # Validate the old database boundary without reading a mutable Run body.
            read_events(
                deps.settings.database,
                boundary,
                EventFilters(),
                limit=1,
                budget=budget,
                connect=deps.connect,
            )
            metadata = _metadata(
                deps, run_id, boundary, params.boundary, budget, after=cursor.after_id
            )
            metadata.pop("page")
            context: dict[str, object] = {
                "run_id": str(run_id),
                "metadata": metadata,
                "back_href": f"/runs/{run_id}/workspace",
                "title": "Artifact 登记分页",
            }
            page = render_page(
                "partials/artifact_metadata.html" if fragment else "workspace.html",
                {**context, "partial": "metadata"},
            )
        elif params.patch is not None and params.cursor is None:
            read_events(
                deps.settings.database,
                boundary,
                EventFilters(),
                limit=1,
                budget=budget,
                connect=deps.connect,
            )
            patch = _patch_context(
                deps, run_id, params.patch, params.boundary, boundary, budget, {}
            )
            page = render_page(
                "partials/diff.html" if fragment else "workspace.html",
                {
                    "patch": patch,
                    "partial": "patch",
                    "run_id": str(run_id),
                    "back_href": f"/runs/{run_id}/workspace",
                    "title": "所选 Patch",
                },
            )
        else:
            raise ReadFailure(422, "workspace_navigation_invalid")
        budget.check()
        return RenderedPage(page)
    if params.cursor is not None:
        raise ReadFailure(422, "boundary_required")
    read = read_workspace_page(
        deps.settings.database, run_id, budget=budget, connect=deps.connect
    )
    boundary = read.boundary
    token = _source_token(deps, boundary, EventParams(filters=EventFilters()))
    metadata = _metadata(deps, run_id, boundary, token, budget)
    page = metadata.pop("page")
    assert isinstance(page, ManifestPage)
    manifests = {item.artifact_id: item for item in page.items}
    discovery = deps.reader.discover_patches(run_id, budget=budget)
    manifests.update((item.artifact_id, item) for item in discovery.items)
    associated = []
    issues = list(discovery.issues)
    association_conflict = read.events.next_sequence is not None or any(
        item.event is None for item in read.events.items
    )
    for item in read.events.items:
        if item.event is None:
            continue
        payload = item.event.payload
        refs = [*item.event.artifacts]
        if isinstance(payload, EvaluationCompleted) and payload.result is not None:
            refs.extend(payload.result.artifacts)
        for ref in refs:
            budget.check()
            if ref.artifact_id not in manifests:
                try:
                    manifests[ref.artifact_id] = deps.reader.read_manifest(
                        run_id, ref.artifact_id, budget=budget
                    )
                except ReadFailure as exc:
                    if exc.status_code not in (404, 409):
                        raise
                    manifests[ref.artifact_id] = ManifestItem(
                        ref.artifact_id,
                        None,
                        (
                            ReadIssue(
                                exc.code,
                                "评测引用的 Artifact 当前不可读",
                                ref.artifact_id,
                            ),
                        ),
                    )
            record = manifests[ref.artifact_id].record
            if ref.type == "workspace_patch" or (
                record is not None and record.type == "workspace_patch"
            ):
                associated.append(ref.artifact_id)
                if record is None or any(
                    value is not None and getattr(record, key) != value
                    for key, value in ref.model_dump().items()
                ):
                    association_conflict = True
    if association_conflict:
        issues.append(
            ReadIssue("evaluation_not_loaded", "评测关联未完成加载；请在详情时间线检查")
        )
    contents = {}
    for item in manifests.values():
        if item.record is not None and item.record.type in {
            "evaluation_config",
            "evaluation_summary",
            "evaluation_report",
        }:
            content = _read_evidence_content(deps, run_id, item, budget)
            if content is not None:
                contents[item.artifact_id] = content
    evidence = adapt_evaluation(contents)
    preliminary = project_evaluations(
        read.run, boundary, read.events.items, tuple(manifests.values()), evidence
    )
    association_conflict = association_conflict or any(
        view.outcome == "conflict" for view in preliminary
    )
    choice = choose_patch(
        discovery, tuple(associated), association_conflict=association_conflict
    )
    selected = params.patch or choice.artifact_id
    if selected is not None and not any(
        item.artifact_id == selected
        and item.record is not None
        and item.record.type == "workspace_patch"
        for item in discovery.items
    ):
        raise ReadFailure(422, "patch_selection_invalid")
    if selected is not None:
        content = _read_evidence_content(deps, run_id, manifests[selected], budget)
        if content is not None:
            contents[selected] = content
    evaluations = project_evaluations(
        read.run,
        boundary,
        read.events.items,
        tuple(manifests.values()),
        adapt_evaluation(contents),
    )
    full_context: dict[str, object] = {
        "partial": None,
        "run_id": str(run_id),
        "title": "Workspace",
        "back_href": f"/runs/{run_id}",
        "as_of": boundary.as_of_sequence,
        "snapshot_version": boundary.latest_snapshot_version,
        "read_at": boundary.read_at.isoformat(),
        "boundary_token": token,
        "status": boundary.run_status.value,
        "issues": (*read.run.issues, *issues),
        "metadata": metadata,
        "evaluations": [
            {
                "attempt": str(view.attempt_id) if view.attempt_id else "未关联",
                "fields": _field_rows(
                    view.fields, deps, boundary, EventParams(filters=EventFilters())
                ),
                "issues": view.issues,
                "artifacts": [
                    {
                        "id": str(identifier),
                        "href": _artifact_href(run_id, identifier, token),
                    }
                    for identifier in view.artifact_ids
                ],
            }
            for view in evaluations
        ],
        "patch": _patch_context(
            deps, run_id, selected, token, boundary, budget, contents
        ),
        "patch_reason": choice.reason if params.patch is None else "用户显式选择",
        "patch_options": [
            {
                "id": str(item.artifact_id),
                "selected": item.artifact_id == selected,
                "metadata": json.dumps(
                    redact_json(item.record.metadata, schema="metadata"),
                    ensure_ascii=False,
                ),
            }
            for item in discovery.items
            if item.record is not None
        ],
    }
    rendered = render_page(
        "partials/workspace_facts.html" if fragment else "workspace.html", full_context
    )
    budget.check()
    return RenderedPage(rendered)


def render_artifact(
    deps: PageDependencies, run_id: UUID, artifact_id: UUID, params: ArtifactParams
) -> RenderedPage:
    budget = _budget(deps)
    source = _decode_source(deps, run_id, params.boundary)
    boundary = source.boundary
    offset = params.offset or 0
    cursor = None
    if params.cursor is not None:
        cursor = deps.signer.decode(params.cursor, purpose="preview")
        if (
            not isinstance(cursor, PreviewCursor)
            or cursor.boundary != boundary
            or cursor.artifact_id != artifact_id
            or (params.offset is not None and params.offset != cursor.offset)
        ):
            raise ReadFailure(422, "preview_cursor_binding")
        offset = cursor.offset
    read_events(
        deps.settings.database,
        boundary,
        EventFilters(),
        limit=1,
        budget=budget,
        connect=deps.connect,
    )
    content = deps.reader.read_content(run_id, artifact_id, budget=budget)
    if cursor is not None and cursor.sha256 != content.record.sha256:
        raise ReadFailure(422, "preview_cursor_binding")
    preview = project_preview(content, offset=offset, budget=budget)
    next_href = None
    if preview.next_offset is not None:
        now = deps.now()
        next_cursor = deps.signer.encode(
            PreviewCursor(
                boundary=boundary,
                artifact_id=artifact_id,
                sha256=content.record.sha256,
                offset=preview.next_offset,
                issued_at=now,
                expires_at=now + timedelta(seconds=LIMITS.cursor_seconds),
            )
        )
        next_href = f"/runs/{run_id}/artifacts/{artifact_id}?" + urlencode(
            {"boundary": params.boundary, "cursor": next_cursor}
        )
    context: dict[str, object] = {
        "run_id": str(run_id),
        "title": "Artifact 预览",
        "back_href": f"/runs/{run_id}/workspace",
        "fields": _field_rows(
            preview.fields, deps, boundary, EventParams(filters=EventFilters())
        ),
        "text": preview.text,
        "offset": preview.offset,
        "inline_continuation": preview.inline_continuation,
        "issues": preview.issues,
        "next_href": next_href,
        "read_at": preview.read_at.isoformat(),
    }
    rendered = render_page("partials/artifact_preview.html", context)
    budget.check()
    return RenderedPage(rendered)
