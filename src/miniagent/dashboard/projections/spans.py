from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.reads import ReadIssue, ScopeRead
from miniagent.dashboard.redaction import redact_text
from miniagent.dashboard.views import SpanView
from miniagent.runtime.events import RunFinished
from miniagent.runtime.state import TERMINAL_STATUSES

_PAIRS = {
    "run": ("run_started", "run_finished"),
    "step": ("step_started", "step_completed"),
    "model": ("model_requested", "model_responded"),
    "tool": ("tool_start", "tool_end"),
    "tool_execution": ("tool_execution_start", "tool_execution_end"),
    "evaluation": ("evaluation_start", "evaluation_completed"),
    "approval_wait": ("approval_requested", "approval_resolved"),
}
_PARENTS = {
    "run": None,
    "step": "run",
    "model": "step",
    "tool": "step",
    "tool_execution": "tool",
    "evaluation": "run",
    "approval_wait": "tool",
}


def project_spans(facts: ScopeRead, boundary: PageBoundary) -> tuple[SpanView, ...]:
    categories = {
        span_id: tuple(
            category
            for category, roles in _PAIRS.items()
            if any(event.type in roles for event in scope.events)
        )
        for span_id, scope in facts.scopes.items()
    }
    if facts.complete and not any(
        "run" in candidates for candidates in categories.values()
    ):
        return ()
    views = []
    for span_id, scope in facts.scopes.items():
        if not scope.events:
            continue
        issues: list[ReadIssue] = []
        candidates = categories[span_id]
        category = candidates[0] if len(candidates) == 1 else "unknown"
        if category == "unknown":
            issues.append(ReadIssue("scope_conflict", "scope 类别不一致"))
            views.append(
                SpanView(
                    redact_text(span_id),
                    category,
                    "conflict",
                    None,
                    None,
                    None,
                    tuple(issues),
                )
            )
            continue
        start_type, end_type = _PAIRS[category]
        starts = [item for item in scope.events if item.type == start_type]
        ends = [item for item in scope.events if item.type == end_type]
        state = "complete"
        duration = None
        if not scope.loaded:
            state = "not_loaded"
            issues.append(ReadIssue("not_loaded", "scope 正文尚未完整加载"))
        elif scope.start_count > 1 or scope.end_count > 1:
            state = "duplicate"
            issues.append(ReadIssue("scope_duplicate", "scope 起止存在重复"))
        elif not starts or not ends:
            state = (
                "open"
                if starts and boundary.run_status not in TERMINAL_STATUSES
                else "incomplete"
            )
            if not starts:
                issues.append(ReadIssue("scope_incomplete", "scope 开始记录缺失"))
        elif (
            ends[0].occurred_at is None
            or starts[0].occurred_at is None
            or ends[0].occurred_at < starts[0].occurred_at
            or ends[0].sequence < starts[0].sequence
        ):
            state = "invalid_time"
            issues.append(ReadIssue("invalid_time", "scope 记录时间或顺序倒退"))
        else:
            assert ends[0].occurred_at is not None and starts[0].occurred_at is not None
            duration = int(
                (ends[0].occurred_at - starts[0].occurred_at).total_seconds() * 1000
            )
        parents = {item.parent_span_id for item in scope.events}
        if len(parents) != 1:
            issues.append(ReadIssue("scope_conflict", "scope 父节点不一致"))
        else:
            parent = next(iter(parents))
            expected = _PARENTS[category]
            if parent is None and expected is not None:
                issues.append(ReadIssue("missing_parent", "scope 父节点缺失"))
            elif parent is not None:
                seen = {span_id}
                ancestor = parent
                while ancestor is not None and ancestor in facts.scopes:
                    if ancestor in seen:
                        issues.append(ReadIssue("scope_cycle", "scope 父链成环"))
                        break
                    seen.add(ancestor)
                    ancestor = next(
                        (item.parent_span_id for item in facts.scopes[ancestor].events),
                        None,
                    )
                parent_scope = facts.scopes.get(parent)
                if parent_scope is None or not parent_scope.loaded:
                    code = (
                        "not_loaded"
                        if not facts.complete
                        or (parent_scope is not None and not parent_scope.loaded)
                        else "missing_parent"
                    )
                    issues.append(
                        ReadIssue(
                            code,
                            "scope 父节点尚未加载"
                            if code == "not_loaded"
                            else "scope 父节点缺失",
                        )
                    )
                elif not parent_scope.events:
                    issues.append(ReadIssue("missing_parent", "scope 父节点缺失"))
                elif categories.get(parent) != (expected,):
                    issues.append(
                        ReadIssue("invalid_hierarchy", "scope 类别层级不合法")
                    )
        for item in ends:
            if (
                item.event is not None
                and isinstance(item.event.payload, RunFinished)
                and item.event.payload.status != boundary.run_status
            ):
                issues.append(ReadIssue("scope_conflict", "结束记录与页面状态不一致"))
        if any(issue.code not in ("not_loaded",) for issue in issues):
            duration = None
        views.append(
            SpanView(
                redact_text(span_id),
                category,
                state,
                duration,
                starts[0].event_id if len(starts) == 1 else None,
                ends[0].event_id if len(ends) == 1 else None,
                tuple(issues),
            )
        )
    return tuple(views)
