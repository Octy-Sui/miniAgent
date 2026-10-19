from decimal import Decimal
from hashlib import sha256
from importlib.resources import files

import orjson
from pydantic import ValidationError

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.projections.run import format_usd
from miniagent.dashboard.reads import (
    CompactionRead,
    ReadIssue,
    StoredEvent,
    StoredRun,
    StoredSnapshot,
)
from miniagent.dashboard.redaction import redact_json, redact_text
from miniagent.dashboard.views import (
    CompactionAttemptView,
    CompactionGroupView,
    EvidenceView,
    FieldView,
    MemoryView,
    SourceTarget,
)
from miniagent.models.messages import AssistantMessage
from miniagent.runtime.context import CompactionSummary
from miniagent.runtime.events import (
    CompactionAttempt,
    CompactionAttemptRecorded,
    CompactionCompleted,
    CompactionFailed,
    CompactionRequested,
)
from miniagent.runtime.state import RunState

_PREFIX = (
    files("miniagent.runtime")
    .joinpath("templates/compaction_v1.j2")
    .read_text()
    .split("{{ summary_json }}", 1)[0]
)


def _group(
    first: int | None, events: list[StoredEvent], closed: bool
) -> CompactionGroupView:
    attempts: dict[int, list[CompactionAttempt]] = {}
    supplemental: dict[int, list[CompactionAttempt]] = {}
    issues = []
    for item in events:
        if item.event is None:
            issues.append(ReadIssue("not_loaded", "部分压缩事实未完成读取"))
            continue
        payload = item.event.payload
        if isinstance(payload, CompactionAttemptRecorded):
            attempts.setdefault(payload.result.attempt, []).append(payload.result)
        elif isinstance(payload, (CompactionCompleted, CompactionFailed)):
            for attempt in payload.attempts:
                supplemental.setdefault(attempt.attempt, []).append(attempt)
    views = []
    for number in sorted(attempts.keys() | supplemental.keys()):
        recorded = attempts.get(number, [])
        endings = supplemental.get(number, [])
        candidates = recorded or endings
        different = any(item != candidates[0] for item in [*candidates, *endings])
        local = []
        if different:
            local.append(
                ReadIssue("attempt_conflict", "同次摘要尝试的费用或 usage 不一致")
            )
        if len(recorded) > 1:
            local.append(ReadIssue("attempt_duplicate", "同次摘要尝试重复记录"))
        chosen = candidates[0]
        cost = (
            chosen.request_cost.total_cost
            if chosen.request_cost is not None and not different
            else None
        )
        fields = [
            FieldView(
                "cost_usd",
                "摘要尝试费用（USD）",
                format_usd(cost) if cost is not None else "未记录",
                "摘要尝试记录",
            )
        ]
        if chosen.usage is not None:
            fields.extend(
                FieldView(key, key, str(value), "摘要尝试记录")
                for key, value in chosen.usage.model_dump().items()
            )
        if chosen.error_code is not None:
            fields.append(
                FieldView(
                    "error_code",
                    "尝试错误代码",
                    redact_text(chosen.error_code),
                    "摘要尝试记录",
                )
            )
        views.append(CompactionAttemptView(number, cost, tuple(fields), tuple(local)))
    if first is None:
        issues.append(ReadIssue("group_unassociated", "压缩事实未关联到唯一首次请求"))
    if not closed:
        issues.append(ReadIssue("group_open", "尚未记录压缩结束"))
    total = (
        sum((view.cost_usd for view in views if view.cost_usd is not None), Decimal(0))
        if closed
        and views
        and all(view.cost_usd is not None for view in views)
        and not any(issue.code == "not_loaded" for issue in issues)
        else None
    )
    return CompactionGroupView(first, tuple(views), total, tuple(issues))


def group_compactions(
    events: tuple[StoredEvent, ...],
) -> tuple[CompactionGroupView, ...]:
    groups = []
    active: list[StoredEvent] = []
    first = None
    for item in sorted(events, key=lambda item: item.sequence):
        payload = item.event.payload if item.event is not None else None
        if isinstance(payload, CompactionRequested) and payload.attempt == 1:
            if active:
                groups.append(_group(first, active, False))
            active = [item]
            first = item.sequence
        elif isinstance(
            payload,
            (
                CompactionRequested,
                CompactionAttemptRecorded,
                CompactionCompleted,
                CompactionFailed,
            ),
        ):
            active.append(item)
        if item.event is None and item.type in (
            "compaction_requested",
            "compaction_attempt_recorded",
            "compaction_completed",
            "compaction_failed",
        ):
            active.append(item)
        if isinstance(payload, (CompactionCompleted, CompactionFailed)) or (
            item.event is None
            and item.type in ("compaction_completed", "compaction_failed")
        ):
            groups.append(_group(first, active, True))
            active = []
            first = None
    if active:
        groups.append(_group(first, active, False))
    return tuple(groups)


def _summary(state: RunState) -> tuple[CompactionSummary | None, str | None]:
    messages = state.conversation.messages
    if (
        len(messages) < 3
        or not isinstance(messages[2], AssistantMessage)
        or messages[2].tool_calls
    ):
        return None, None
    text = messages[2].content
    if not text or not text.startswith(_PREFIX):
        return None, text
    try:
        return CompactionSummary.model_validate(parse_json(text[len(_PREFIX) :])), text
    except (ContentError, ValidationError):
        return None, text


def _integrity(
    state: RunState, boundary: PageBoundary, facts: CompactionRead
) -> tuple[ReadIssue, ...]:
    issues = []
    current = state.context.current_version
    version = current
    seen = set()
    while version is not None:
        if (
            len(seen) >= LIMITS.compaction_versions
            or version in facts.unloaded_versions
        ):
            issues.append(ReadIssue("not_loaded", "压缩版本链尚未完整加载"))
            break
        if version in seen:
            issues.append(ReadIssue("compaction_chain_inconsistent", "压缩版本链成环"))
            break
        seen.add(version)
        matches = [
            item
            for item in facts.events
            if item.event is not None
            and isinstance(item.event.payload, CompactionCompleted)
            and item.event.payload.current_version == version
        ]
        if len(matches) != 1:
            issues.append(
                ReadIssue(
                    "not_loaded" if not matches else "compaction_duplicate",
                    "压缩版本尚未加载" if not matches else "压缩版本重复记录",
                )
            )
            break
        item = matches[0]
        assert item.event is not None and isinstance(
            item.event.payload, CompactionCompleted
        )
        payload = item.event.payload
        if (
            payload.current_version != (payload.previous_version or 0) + 1
            or payload.event_sequence != item.sequence
            or item.sequence > boundary.as_of_sequence
        ):
            issues.append(
                ReadIssue("compaction_inconsistent", "压缩版本或事件顺序不一致")
            )
        if version == current:
            _, text = _summary(state)
            if (
                text is not None
                and sha256(text.encode()).hexdigest() != payload.summary_sha256
            ):
                issues.append(
                    ReadIssue("summary_hash_mismatch", "摘要完整消息摘要值不一致")
                )
        source = facts.snapshots.get(payload.source_snapshot_version or 0)
        new = facts.snapshots.get(payload.new_snapshot_version or 0)
        if (
            source is None
            or new is None
            or source.snapshot is None
            or new.snapshot is None
        ):
            issues.append(ReadIssue("not_loaded", "压缩来源 Snapshot 尚未完整加载"))
            version = payload.previous_version
            continue
        if (
            source.run_id != boundary.run_id
            or new.run_id != boundary.run_id
            or source.snapshot_id != payload.source_snapshot_id
            or new.snapshot_id != payload.new_snapshot_id
            or source.version >= new.version
            or new.version > boundary.latest_snapshot_version
            or source.event_sequence != payload.source_event_sequence
            or new.event_sequence != item.sequence
        ):
            issues.append(
                ReadIssue("compaction_inconsistent", "压缩 Snapshot 身份或边界不一致")
            )
        old_state = source.snapshot.state
        new_state = new.snapshot.state
        if any(
            issue.field_path == ("context", "current_version")
            for snapshot in (source, new)
            for issue in snapshot.issues
        ):
            issues.append(ReadIssue("not_loaded", "Snapshot 压缩版本字段未记录"))
        elif (
            old_state.context.current_version != payload.previous_version
            or new_state.context.current_version != version
        ):
            issues.append(
                ReadIssue("compaction_inconsistent", "Snapshot 压缩版本不一致")
            )
        old_messages = old_state.conversation.messages
        new_messages = new_state.conversation.messages
        start, end = payload.covered_messages
        if (
            start != 2
            or not start < end <= len(old_messages)
            or new_messages[:2] != old_messages[:2]
            or new_messages[3:] != old_messages[end:]
        ):
            issues.append(
                ReadIssue("compaction_inconsistent", "压缩覆盖或保留消息不一致")
            )
        covered = tuple(
            step.step_number
            for step in old_state.conversation.completed_steps
            if start <= step.message_start and step.message_end <= end
        )
        retained = tuple(
            step
            for step in old_state.conversation.completed_steps
            if step.message_start >= end
        )
        if (
            covered != payload.covered_steps
            or not retained
            or retained[0].step_number != payload.retained_first_step
            or end != retained[0].message_start
        ):
            issues.append(ReadIssue("compaction_inconsistent", "压缩 Step 范围不一致"))
        if (
            len(new_messages) < 3
            or not isinstance(new_messages[2], AssistantMessage)
            or new_messages[2].tool_calls
            or sha256((new_messages[2].content or "").encode()).hexdigest()
            != payload.summary_sha256
        ):
            issues.append(
                ReadIssue(
                    "summary_hash_mismatch", "Snapshot 中摘要完整消息摘要值不一致"
                )
            )
        if version == current and (
            state.conversation.messages[: len(new_messages)] != new_messages
            or state.conversation.completed_steps[
                : len(new_state.conversation.completed_steps)
            ]
            != new_state.conversation.completed_steps
        ):
            issues.append(
                ReadIssue(
                    "compaction_inconsistent", "当前保留历史与已提交 Snapshot 不一致"
                )
            )
        version = payload.previous_version
    return tuple(dict.fromkeys(issues))


def _project(
    state: RunState | None,
    boundary: PageBoundary,
    facts: CompactionRead,
    issues: tuple[ReadIssue, ...],
) -> MemoryView:
    groups = group_compactions(facts.events)
    if state is None or any(
        issue.code == "field_not_recorded"
        and issue.field_path == ("context", "current_version")
        for issue in issues
    ):
        return MemoryView(
            None,
            (FieldView("memory", "Task Memory", "未完成读取", "Run"),),
            (),
            groups,
            (*issues, ReadIssue("not_loaded", "当前摘要状态尚未完整加载")),
        )
    version = state.context.current_version
    if version is None:
        return MemoryView(
            None,
            (FieldView("memory", "Task Memory", "尚未生成持久化 Task Memory", "Run"),),
            (),
            groups,
            issues,
        )
    summary, text = _summary(state)
    integrity = _integrity(state, boundary, facts)
    if summary is None:
        fields = (
            FieldView(
                "summary_text",
                "摘要文本",
                redact_text(text) if text else "未记录",
                "摘要记录",
            ),
        )
        return MemoryView(
            version,
            fields,
            (),
            groups,
            (*issues, *integrity, ReadIssue("summary_invalid", "摘要格式或模板不支持")),
        )
    matches = [
        item.event.payload
        for item in facts.events
        if item.event is not None
        and isinstance(item.event.payload, CompactionCompleted)
        and item.event.payload.current_version == version
    ]
    target = None
    if len(matches) == 1:
        snapshot = facts.snapshots.get(matches[0].new_snapshot_version or 0)
        if (
            snapshot is not None
            and snapshot.run_id == boundary.run_id
            and snapshot.version <= boundary.latest_snapshot_version
            and snapshot.event_sequence <= boundary.as_of_sequence
        ):
            target = SourceTarget(
                boundary.run_id, snapshot_version=snapshot.version, start=2, end=3
            )
    values = redact_json(summary.model_dump(mode="json"), schema="summary")
    assert isinstance(values, dict)
    fields = tuple(
        FieldView(
            key,
            key,
            value if isinstance(value, str) else orjson.dumps(value).decode(),
            "摘要记录",
            target,
        )
        for key, value in values.items()
        if key != "verified_evidence"
    )
    evidence = tuple(
        EvidenceView(redact_text(item.claim), "not_loaded", "未完成校验", None, ())
        for item in summary.verified_evidence
    )
    return MemoryView(
        version, fields, evidence, groups, tuple(dict.fromkeys((*issues, *integrity)))
    )


def project_memory(
    run: StoredRun, boundary: PageBoundary, facts: CompactionRead
) -> MemoryView:
    return _project(
        run.record.state if run.record is not None else None,
        boundary,
        facts,
        run.issues,
    )


def project_historical_memory(
    snapshot: StoredSnapshot, boundary: PageBoundary, facts: CompactionRead
) -> MemoryView:
    return _project(
        snapshot.snapshot.state if snapshot.snapshot is not None else None,
        boundary,
        facts,
        snapshot.issues,
    )
