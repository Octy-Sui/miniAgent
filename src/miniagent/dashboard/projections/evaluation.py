import json
from collections.abc import Iterable
from uuid import UUID

from pydantic import JsonValue

from miniagent.artifacts.contracts import ArtifactRecord, ArtifactRef
from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.evaluation_evidence import EvaluationEvidence
from miniagent.dashboard.reads import ManifestItem, ReadIssue, StoredEvent, StoredRun
from miniagent.dashboard.redaction import redact_json
from miniagent.dashboard.views import EvaluationAttemptView, FieldView, SourceTarget
from miniagent.runtime.events import EvaluationCompleted
from miniagent.runtime.state import RunStatus

_CORE = (
    "evaluation_config",
    "evaluation_summary",
    "evaluation_report",
    "workspace_patch",
)
_LOGS = (
    "evaluation_stdout",
    "evaluation_stderr",
    "evaluation_test_output",
    "evaluation_instance_log",
)


def _uuid(value: JsonValue | None) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _field(
    key: str,
    label: str,
    value: JsonValue,
    source_label: str,
    source: SourceTarget | None = None,
) -> FieldView:
    redacted = redact_json(value)
    text = (
        "未记录"
        if value is None
        else redacted
        if isinstance(redacted, str)
        else json.dumps(redacted, ensure_ascii=False)
    )
    try:
        encoded = text.encode("utf-8")
        if len(encoded) > LIMITS.scalar_bytes:
            suffix = "…（已截短，见来源预览）"
            text = (
                encoded[: LIMITS.scalar_bytes - len(suffix.encode())].decode(
                    "utf-8", errors="ignore"
                )
                + suffix
            )
    except UnicodeError:
        text = "不可显示的文本"
    return FieldView(key, label, text, source_label, source)


def _refs(refs: Iterable[ArtifactRef]) -> tuple[str, ...]:
    return tuple(sorted(ref.model_dump_json() for ref in refs))


def _attempt(
    attempt: UUID,
    members: tuple[ManifestItem, ...],
    records: dict[UUID, ArtifactRecord],
    duplicates: set[UUID],
    run: StoredRun,
    boundary: PageBoundary,
    events: tuple[StoredEvent, ...],
    evidence: EvaluationEvidence,
) -> EvaluationAttemptView:
    issues = []
    seen = set()
    flags: set[str] = set()

    def note(code: str, message: str, subject: UUID | None = None) -> None:
        flags.add(code)
        issue = ReadIssue(code, message, subject_id=subject)
        if issue not in seen:
            seen.add(issue)
            issues.append(issue)

    fields = [
        _field("attempt_id", "评测 attempt", str(attempt), "Artifact metadata"),
        _field("run_status", "Runtime 最后记录状态", boundary.run_status.value, "Run"),
    ]
    member_ids = {item.artifact_id for item in members}
    artifact_ids = set(member_ids)
    related = []
    for item in events:
        if (
            item.run_id != boundary.run_id
            or item.sequence > boundary.as_of_sequence
            or item.event is None
            or not isinstance(item.event.payload, EvaluationCompleted)
        ):
            continue
        result = item.event.payload.result
        all_refs = [
            *item.event.artifacts,
            *(result.artifacts if result is not None else ()),
        ]
        if any(ref.artifact_id in member_ids for ref in all_refs):
            related.append(item)
    if not related:
        note("evidence_incomplete", "尚未读取支持该组的 EvaluationCompleted")
    signatures = set()
    for item in related:
        assert item.event is not None and isinstance(
            item.event.payload, EvaluationCompleted
        )
        payload = item.event.payload
        signatures.add((payload.passed, _refs(item.event.artifacts)))
        target = SourceTarget(boundary.run_id, event_id=item.event_id)
        fields.append(
            _field(
                f"passed_{item.sequence}",
                "已记录的评测结果",
                "true" if payload.passed else "false",
                f"Event #{item.sequence}",
                target,
            )
        )
        if payload.result is not None:
            fields.append(
                _field(
                    f"result_passed_{item.sequence}",
                    "EvaluationResult.passed",
                    "true" if payload.result.passed else "false",
                    f"Event #{item.sequence}",
                    target,
                )
            )
            if payload.result.passed != payload.passed or _refs(
                payload.result.artifacts
            ) != _refs(item.event.artifacts):
                note(
                    "evidence_conflict",
                    "EvaluationCompleted 与扩展 result 的记录不一致",
                    item.event_id,
                )
    if len(signatures) > 1:
        note(
            "evidence_conflict", "同 attempt 的多个 EvaluationCompleted 引用或结论冲突"
        )
    selected = related[0].event if related else None
    references = selected.artifacts if selected is not None else []
    by_kind: dict[str, dict[UUID, ArtifactRef]] = {kind: {} for kind in _CORE}
    for ref in references:
        artifact_ids.add(ref.artifact_id)
        record = records.get(ref.artifact_id)
        kind = record.type if record is not None else ref.type
        if kind in by_kind:
            existing = by_kind[kind].get(ref.artifact_id)
            if existing is not None and existing != ref:
                note(
                    "evidence_conflict", "同一 Artifact 引用字段不一致", ref.artifact_id
                )
            by_kind[kind][ref.artifact_id] = ref
        mandatory = kind in _CORE
        if record is None:
            if mandatory:
                note(
                    "evidence_incomplete",
                    "必要 Artifact 当前未发现登记",
                    ref.artifact_id,
                )
            continue
        if record.run_id != boundary.run_id or ref.artifact_id in duplicates:
            note("evidence_conflict", "Artifact 归属或重复登记不一致", ref.artifact_id)
        for key, value in ref.model_dump().items():
            if value is None:
                if mandatory and key in ("sha256", "size_bytes"):
                    note(
                        "evidence_incomplete",
                        "必要 Artifact 引用的摘要或大小未记录",
                        ref.artifact_id,
                    )
            elif getattr(record, key) != value:
                note(
                    "evidence_conflict",
                    "Artifact 引用与登记字段不一致",
                    ref.artifact_id,
                )
        recorded_attempt = _uuid(record.metadata.get("attempt_id"))
        if (
            mandatory
            and record.type.startswith("evaluation_")
            and recorded_attempt is None
        ):
            note(
                "evidence_incomplete",
                "必要评测 Artifact 的 attempt 身份未记录或无效",
                ref.artifact_id,
            )
        if (
            record.type.startswith("evaluation_")
            and recorded_attempt is not None
            and recorded_attempt != attempt
        ):
            note(
                "evidence_conflict",
                "同一评测事件混入其他 attempt 的 Artifact",
                ref.artifact_id,
            )
        verified = evidence.contents.get(ref.artifact_id)
        if verified is not None and verified.record != record:
            note(
                "evidence_conflict",
                "读取内容时的 manifest 与页面登记不一致",
                ref.artifact_id,
            )
        if mandatory and (verified is None or verified.content is None):
            note(
                "evidence_incomplete", "必要 Artifact 内容尚未完成校验", ref.artifact_id
            )
        if verified is not None and any(
            issue.code
            in (
                "artifact_hash_mismatch",
                "artifact_size_mismatch",
                "artifact_reference_mismatch",
            )
            for issue in verified.issues
        ):
            note("evidence_conflict", "Artifact 内容完整性校验不一致", ref.artifact_id)
    chosen: dict[str, UUID | None] = {}
    for kind, candidates in by_kind.items():
        if not candidates:
            note("evidence_incomplete", "评测事件未明确引用必要的 " + kind)
        elif len(candidates) > 1:
            note("evidence_conflict", "评测事件引用了多个 " + kind)
        chosen[kind] = next(iter(candidates)) if len(candidates) == 1 else None
    config_id = chosen["evaluation_config"]
    summary_id = chosen["evaluation_summary"]
    report_id = chosen["evaluation_report"]
    patch_id = chosen["workspace_patch"]
    config = evidence.configs.get(config_id) if config_id is not None else None
    summary = evidence.summaries.get(summary_id) if summary_id is not None else None
    report = evidence.reports.get(report_id) if report_id is not None else None
    for identifier in (config_id, summary_id, report_id, patch_id):
        if identifier is not None:
            artifact_ids.add(identifier)
    # Metadata identity is independently checkable even when a necessary sibling is absent.
    core_ids = {identifier for identifier in chosen.values() if identifier is not None}
    for item in members:
        used = item.artifact_id in core_ids or selected is None
        parsed = evidence.configs.get(item.artifact_id) or evidence.summaries.get(
            item.artifact_id
        )
        if parsed is None or item.record is None:
            continue
        if parsed.run_id != boundary.run_id or parsed.attempt_id != attempt:
            note(
                "evidence_conflict" if used else "unreferenced_identity_conflict",
                "config/summary 的 Run 或 attempt 与登记不一致",
                item.artifact_id,
            )
        instance = item.record.metadata.get("instance_id")
        if not isinstance(instance, str) or not instance:
            note(
                "evidence_incomplete" if used else "unreferenced_identity_incomplete",
                "评测 Artifact 的实例身份未记录",
                item.artifact_id,
            )
        elif parsed.instance_id != instance:
            note(
                "evidence_conflict" if used else "unreferenced_identity_conflict",
                "config/summary 的实例身份与登记不一致",
                item.artifact_id,
            )
    for kind, parsed, identifier in (
        ("config", config, config_id),
        ("summary", summary, summary_id),
        ("report", report, report_id),
    ):
        if parsed is None:
            note("evidence_incomplete", kind + " 尚未完成结构或版本校验", identifier)
    active_ids = member_ids | {
        identifier for identifier in chosen.values() if identifier is not None
    }
    for issue in evidence.issues:
        if issue.subject_id in active_ids:
            issues.append(issue)
            if issue.subject_id in core_ids and issue.code in (
                "evidence_unsupported",
                "harness_unsupported",
            ):
                note(
                    "evidence_unsupported",
                    "当前证据形状或 harness 版本不支持",
                    issue.subject_id,
                )
    positives = []
    negatives = []
    if boundary.run_status == RunStatus.SUCCEEDED:
        positives.append("Run")
    elif boundary.run_status in (RunStatus.FAILED, RunStatus.CANCELLED):
        negatives.append("Run")
    else:
        note("evidence_incomplete", "Run 尚未记录 succeeded 终态")
    if (
        run.envelope.run_id != boundary.run_id
        or run.envelope.status != boundary.run_status
    ):
        note("evidence_conflict", "Run 与页面读取边界不一致")
    for item in related:
        assert item.event is not None and isinstance(
            item.event.payload, EvaluationCompleted
        )
        (positives if item.event.payload.passed else negatives).append("Event")
    if config is not None:
        fields.extend(
            (
                _field(
                    "instance_id", "实例", config.instance_id, f"Artifact {config_id}"
                ),
                _field(
                    "harness_version",
                    "harness 版本",
                    config.harness_version,
                    f"Artifact {config_id}",
                ),
            )
        )
        if config.harness_version != "5.0.2":
            note("evidence_unsupported", "harness 版本不支持", config_id)
    if config is not None:
        fields.extend(
            (
                _field(
                    "patch_sha256",
                    "config 记录的补丁 SHA-256",
                    config.patch_sha256,
                    f"Artifact {config_id}",
                ),
                _field(
                    "submission_id",
                    "config 记录的 submission",
                    str(config.submission_id),
                    f"Artifact {config_id}",
                ),
            )
        )
    if summary is not None:
        fields.extend(
            (
                _field(
                    "summary_instance_id",
                    "summary 记录的实例",
                    summary.instance_id,
                    f"Artifact {summary_id}",
                ),
                _field(
                    "summary_patch_sha256",
                    "summary 记录的补丁 SHA-256",
                    summary.patch_sha256,
                    f"Artifact {summary_id}",
                ),
                _field(
                    "summary_submission_id",
                    "summary 记录的 submission",
                    str(summary.submission_id),
                    f"Artifact {summary_id}",
                ),
            )
        )
        fields.append(
            _field(
                "verdict",
                "已记录 summary verdict",
                summary.verdict,
                f"Artifact {summary_id}",
            )
        )
        if summary.verdict == "report_resolved":
            positives.append("summary")
        elif summary.verdict in ("failed", "cancelled"):
            negatives.append("summary")
        else:
            note("evidence_incomplete", "summary 尚未记录最终 verdict", summary_id)
    if (
        config is not None
        and summary is not None
        and (
            config.run_id,
            config.attempt_id,
            config.instance_id,
            config.submission_id,
            config.patch_artifact_id,
            config.patch_sha256,
        )
        != (
            summary.run_id,
            summary.attempt_id,
            summary.instance_id,
            summary.submission_id,
            summary.patch_artifact_id,
            summary.patch_sha256,
        )
    ):
        note("evidence_conflict", "config 与最终 summary 的身份或补丁引用不一致")
    patch = records.get(patch_id) if patch_id is not None else None
    if patch is not None:
        submission = _uuid(patch.metadata.get("submission_id"))
        if submission is None:
            note("evidence_incomplete", "补丁 submission 身份未记录或无效", patch_id)
        for document in (config, summary):
            if document is not None and (
                document.patch_artifact_id != patch.artifact_id
                or document.patch_sha256 != patch.sha256
                or (submission is not None and document.submission_id != submission)
            ):
                note(
                    "evidence_conflict",
                    "评测记录与补丁 ID/hash/submission 不一致",
                    patch_id,
                )
        if config is not None:
            for key, value in config.patch_ref.model_dump().items():
                if value is None and key in ("sha256", "size_bytes"):
                    note("evidence_incomplete", "config 的补丁引用字段未记录", patch_id)
                elif value is not None and getattr(patch, key) != value:
                    note(
                        "evidence_conflict",
                        "config.patch_ref 与登记补丁不一致",
                        patch_id,
                    )
        verified = evidence.contents.get(patch.artifact_id)
        if verified is not None and verified.content == b"":
            negatives.append("patch")
    if report is not None:
        passing = (
            report.resolved
            and report.patch_exists
            and not report.patch_is_None
            and report.patch_successfully_applied
            and not report.infra_failure
        )
        (positives if passing else negatives).append("report")
        if report.resolved and not passing:
            note(
                "evidence_conflict",
                "report 的 resolved 与 patch/apply/infra 事实不一致",
                report_id,
            )
        fields.extend(
            (
                _field(
                    "resolved",
                    "报告记录 resolved",
                    report.resolved,
                    f"Artifact {report_id}",
                ),
                _field(
                    "infra_failure",
                    "报告记录 infra_failure",
                    report.infra_failure,
                    f"Artifact {report_id}",
                ),
            )
        )
        fields.extend(
            _field(
                key, "报告记录 " + key, getattr(report, key), f"Artifact {report_id}"
            )
            for key in ("patch_exists", "patch_is_None", "patch_successfully_applied")
        )
        if report.tests_status is not None:
            for name, group in (
                ("FAIL_TO_PASS", report.tests_status.FAIL_TO_PASS),
                ("PASS_TO_PASS", report.tests_status.PASS_TO_PASS),
            ):
                fields.extend(
                    (
                        _field(
                            name + "_success",
                            "报告记录的测试 · " + name + " success",
                            group.success,
                            f"Artifact {report_id}",
                        ),
                        _field(
                            name + "_failure",
                            "报告记录的测试 · " + name + " failure",
                            group.failure,
                            f"Artifact {report_id}",
                        ),
                    )
                )
    if positives and negatives:
        note("evidence_conflict", "已记录通过声称与明确否定事实并存")
    if positives and any(
        issue.code == "report_tests_invalid" and issue.subject_id in core_ids
        for issue in issues
    ):
        note("evidence_conflict", "已记录通过声称与报告测试集合问题并存")
    for kind in _LOGS:
        if not any(
            item.record is not None and item.record.type == kind for item in members
        ):
            note("log_missing", "当前未发现登记的 " + kind)
    if "evidence_conflict" in flags:
        outcome = "conflict"
    elif "evidence_unsupported" in flags:
        outcome = "unsupported"
    elif "evidence_incomplete" in flags:
        outcome = "incomplete"
    elif negatives:
        outcome = "recorded_failure"
    else:
        outcome = "recorded_pass"
    titles = {
        "recorded_pass": "已记录独立评测通过",
        "conflict": "证据冲突",
        "incomplete": "证据不完整",
        "unsupported": "证据形状或版本不支持",
        "recorded_failure": "已记录评测未通过",
    }
    fields.insert(0, _field("title", "评测证据", titles[outcome], "持久化事实投影"))
    fields.insert(
        1,
        _field(
            "coverage",
            "目标覆盖说明",
            "目标测试清单未持久化，无法复核完整覆盖",
            "当前生产者契约",
        ),
    )
    for kind, identifier in chosen.items():
        fields.append(
            _field(
                kind + "_id",
                kind + " Artifact ID",
                str(identifier) if identifier is not None else None,
                "EvaluationCompleted artifacts",
            )
        )
    return EvaluationAttemptView(
        attempt, outcome, tuple(fields), tuple(sorted(artifact_ids)), tuple(issues)
    )


def project_evaluations(
    run: StoredRun,
    boundary: PageBoundary,
    events: tuple[StoredEvent, ...],
    manifests: tuple[ManifestItem, ...],
    evidence: EvaluationEvidence,
) -> tuple[EvaluationAttemptView, ...]:
    if run.workspace_kind == "local":
        return ()
    records = {}
    duplicates = set()
    groups: dict[UUID | None, list[ManifestItem]] = {}
    for item in manifests:
        if item.record is not None:
            previous = records.get(item.artifact_id)
            if previous is not None and previous != item.record:
                duplicates.add(item.artifact_id)
            records[item.artifact_id] = item.record
            if not item.record.type.startswith("evaluation_"):
                continue
            attempt = _uuid(item.record.metadata.get("attempt_id"))
        else:
            attempt = None
        groups.setdefault(attempt, []).append(item)
    views = [
        _attempt(
            attempt,
            tuple(members),
            records,
            duplicates,
            run,
            boundary,
            events,
            evidence,
        )
        for attempt, members in sorted(
            ((key, value) for key, value in groups.items() if key is not None),
            key=lambda pair: pair[0],
        )
    ]
    if None in groups:
        members = groups[None]
        views.append(
            EvaluationAttemptView(
                None,
                "incomplete",
                (_field("title", "评测证据", "未关联评测证据", "Artifact metadata"),),
                tuple(sorted(item.artifact_id for item in members)),
                (ReadIssue("evidence_incomplete", "attempt 身份未记录或无效"),),
            )
        )
    return tuple(views)
