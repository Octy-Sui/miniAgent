from collections import Counter
from uuid import UUID

from pydantic import JsonValue

from miniagent.dashboard.reads import EventBatch, ReadIssue, StoredEvent, ToolFacts
from miniagent.dashboard.redaction import redact_json, redact_text
from miniagent.dashboard.views import EventView, ToolView
from miniagent.runtime.events import ToolEnded, ToolScopePayload

_SUMMARIES = {
    "run_started": "开始运行",
    "run_finished": "记录运行结束",
    "step_started": "开始 Step",
    "step_completed": "完成 Step",
    "model_requested": "请求模型",
    "model_responded": "模型已响应",
    "tool_start": "开始工具调用",
    "tool_end": "记录工具结果",
    "tool_execution_start": "开始工具执行",
    "tool_execution_end": "记录执行结束",
    "evaluation_start": "开始评测",
    "evaluation_completed": "记录评测结论",
    "approval_requested": "请求审批",
    "approval_resolved": "记录审批结果",
    "approval_invalidated": "审批已失效",
    "compaction_requested": "请求摘要",
    "compaction_attempt_recorded": "记录摘要尝试",
    "compaction_completed": "摘要已提交",
    "compaction_failed": "摘要失败",
}
_TOOL_ROLES = frozenset(
    ("tool_start", "tool_end", "tool_execution_start", "tool_execution_end")
)


def _hide_locations(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: "[本机位置已隐藏]"
            if key in ("uri", "source_dir", "environment_refs")
            else _hide_locations(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_hide_locations(child) for child in value]
    return value


def project_events(batch: EventBatch) -> tuple[EventView, ...]:
    return tuple(
        EventView(
            item.event_id,
            item.sequence,
            redact_text(item.type),
            item.step_number,
            _SUMMARIES.get(item.type, "已记录事件")
            if item.event is not None
            else "正文未完成读取或不支持",
            redact_json(
                _hide_locations(
                    item.event.payload.model_dump(mode="json", exclude_unset=True)
                ),
                schema="event",
            )
            if item.event is not None
            else None,
            item.issues,
            item.occurred_at,
        )
        for item in batch.items
    )


def _tool_key(item: StoredEvent) -> tuple[UUID, int, str] | None:
    if (
        item.event is not None
        and item.type in _TOOL_ROLES
        and item.step_number is not None
        and isinstance(item.event.payload, ToolScopePayload)
    ):
        return item.run_id, item.step_number, item.event.payload.tool_call_id
    return None


def project_tools(
    events: tuple[StoredEvent, ...], *, facts: ToolFacts | None = None
) -> tuple[ToolView, ...]:
    keys = sorted({key for event in events if (key := _tool_key(event)) is not None})
    source = facts.events if facts is not None else events
    tools = []
    for key in keys:
        matching = tuple(event for event in source if _tool_key(event) == key)
        counts = Counter(event.type for event in matching)
        if facts is not None:
            counts = Counter(
                {
                    role: facts.counts.get((key[1], key[2], role), 0)
                    for role in _TOOL_ROLES
                }
            )
        issues = list(facts.issues if facts is not None else ())
        duplicate = any(count > 1 for count in counts.values())
        if duplicate:
            issues.append(ReadIssue("tool_duplicate", "工具角色存在重复记录"))
        starts = [event for event in matching if event.type == "tool_start"]
        ends = [event for event in matching if event.type == "tool_end"]
        if not starts or not ends:
            if facts is None or any(item.event is None for item in source) or issues:
                issues.append(ReadIssue("not_loaded", "工具起止尚未完整加载"))
            else:
                issues.append(ReadIssue("source_missing", "工具起止记录缺失"))
        status = completeness = None
        exit_code = None
        conflict = False
        if len(starts) == len(ends) == 1:
            first, last = starts[0], ends[0]
            assert first.event is not None and last.event is not None
            a, b = first.event.payload, last.event.payload
            assert isinstance(a, ToolScopePayload) and isinstance(b, ToolScopePayload)
            conflict = (a.call_index, a.tool_name, first.span_id) != (
                b.call_index,
                b.tool_name,
                last.span_id,
            )
            if conflict:
                issues.append(ReadIssue("tool_conflict", "工具关联字段不一致"))
        if len(ends) == 1 and not duplicate and not conflict:
            event = ends[0].event
            assert event is not None and isinstance(event.payload, ToolEnded)
            status = redact_text(event.payload.status)
            result = event.payload.result
            if result is not None:
                output = result.output
                if (
                    "output" in result.model_fields_set
                    and set(type(output).model_fields) <= output.model_fields_set
                ):
                    completeness = (
                        "truncated"
                        if any(
                            (
                                output.stdout_truncated,
                                output.stderr_truncated,
                                output.content_truncated,
                                output.omitted_fields,
                            )
                        )
                        else "complete"
                    )
                code = (result.data or {}).get("exit_code")
                exit_code = code if type(code) is int else None
        tools.append(
            ToolView(
                key[0],
                key[1],
                redact_text(key[2]),
                status,
                completeness,
                exit_code,
                tuple(issues),
            )
        )
    return tuple(tools)


def step_is_partial(events: tuple[StoredEvent, ...], step_number: int) -> bool:
    roles = Counter(event.type for event in events if event.step_number == step_number)
    return roles["step_started"] != 1 or roles["step_completed"] != 1
