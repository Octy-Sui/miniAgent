from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.reads import ReadIssue, VerifiedContent
from miniagent.evaluation.swebench_report import InstanceReport


class _EvaluationIdentity(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)
    run_id: UUID
    attempt_id: UUID
    submission_id: UUID
    patch_artifact_id: UUID
    instance_id: str = Field(min_length=1)
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvaluationConfig(_EvaluationIdentity):
    patch_ref: ArtifactRef
    harness_version: str


class EvaluationSummary(_EvaluationIdentity):
    scope: Literal["evaluation_evidence_snapshot"]
    terminal_status_source: Literal["Run"]
    verdict: Literal["collecting", "cancelled", "failed", "report_resolved"]


@dataclass(frozen=True)
class EvaluationEvidence:
    configs: dict[UUID, EvaluationConfig]
    summaries: dict[UUID, EvaluationSummary]
    reports: dict[UUID, InstanceReport]
    contents: dict[UUID, VerifiedContent]
    issues: tuple[ReadIssue, ...]


def read_recorded_report(content: bytes, *, instance_id: str) -> InstanceReport:
    document = parse_json(content)
    if not isinstance(document, dict):
        raise ContentError("report_fields_invalid")
    if not instance_id or set(document) != {instance_id}:
        raise ContentError("report_instance_mismatch")
    try:
        report = InstanceReport.model_validate(document[instance_id])
    except ValidationError as exc:
        raise ContentError("report_fields_invalid") from exc
    if report.tests_status is None:
        raise ContentError("report_fields_invalid")
    for group in (report.tests_status.FAIL_TO_PASS, report.tests_status.PASS_TO_PASS):
        success, failure = set(group.success), set(group.failure)
        if (
            len(success) != len(group.success)
            or len(failure) != len(group.failure)
            or success & failure
            or (report.resolved and failure)
        ):
            raise ContentError("report_tests_invalid")
    return report


def adapt_evaluation(contents: dict[UUID, VerifiedContent]) -> EvaluationEvidence:
    configs: dict[UUID, EvaluationConfig] = {}
    summaries: dict[UUID, EvaluationSummary] = {}
    reports: dict[UUID, InstanceReport] = {}
    issues = []
    for artifact_id, verified in contents.items():
        record = verified.record
        if record.artifact_id != artifact_id:
            issues.append(
                ReadIssue(
                    "artifact_identity_conflict",
                    "内容身份与登记索引不一致",
                    subject_id=artifact_id,
                )
            )
            continue
        if record.type not in ("evaluation_config", "evaluation_summary"):
            continue
        if verified.content is None:
            issues.append(
                ReadIssue(
                    "evidence_not_loaded",
                    "评测证据内容尚未完成校验",
                    subject_id=artifact_id,
                )
            )
            continue
        model = (
            EvaluationConfig
            if record.type == "evaluation_config"
            else EvaluationSummary
        )
        try:
            data = parse_json(verified.content)
            if not isinstance(data, dict) or not any(
                key in data for key in model.model_fields
            ):
                issues.append(
                    ReadIssue(
                        "evidence_unsupported",
                        "评测证据形状不支持",
                        subject_id=artifact_id,
                    )
                )
                continue
            parsed = model.model_validate_json(verified.content, strict=True)
        except ContentError as exc:
            issues.append(
                ReadIssue(exc.code, "评测 JSON 未通过安全解析", subject_id=artifact_id)
            )
            continue
        except ValidationError as exc:
            for error in exc.errors(
                include_input=False, include_context=False, include_url=False
            ):
                issues.append(
                    ReadIssue(
                        "evidence_fields_invalid",
                        "评测证据字段不支持",
                        subject_id=artifact_id,
                        field_path=error["loc"],
                    )
                )
            continue
        if isinstance(parsed, EvaluationConfig):
            configs[artifact_id] = parsed
        else:
            summaries[artifact_id] = parsed
    for artifact_id, verified in contents.items():
        record = verified.record
        if record.type != "evaluation_report" or record.artifact_id != artifact_id:
            continue
        if verified.content is None:
            issues.append(
                ReadIssue(
                    "evidence_not_loaded",
                    "报告内容尚未完成校验",
                    subject_id=artifact_id,
                )
            )
            continue
        instance = record.metadata.get("instance_id")
        attempt = record.metadata.get("attempt_id")
        try:
            attempt_id = UUID(attempt) if isinstance(attempt, str) else None
        except ValueError:
            attempt_id = None
        if not isinstance(instance, str) or not instance or attempt_id is None:
            issues.append(
                ReadIssue(
                    "report_identity_unsupported",
                    "报告登记身份未记录或无效",
                    subject_id=artifact_id,
                )
            )
            continue
        candidates = [
            config
            for config in configs.values()
            if config.run_id == record.run_id
            and config.attempt_id == attempt_id
            and config.instance_id == instance
        ]
        if not candidates:
            issues.append(
                ReadIssue(
                    "harness_not_recorded",
                    "报告对应的 harness 版本尚未记录",
                    subject_id=artifact_id,
                )
            )
            continue
        if not any(config.harness_version == "5.0.2" for config in candidates):
            issues.append(
                ReadIssue(
                    "harness_unsupported",
                    "报告 harness 版本不支持",
                    subject_id=artifact_id,
                )
            )
            continue
        try:
            reports[artifact_id] = read_recorded_report(
                verified.content, instance_id=instance
            )
        except ContentError as exc:
            issues.append(
                ReadIssue(
                    exc.code, "已记录报告不支持或存在字段问题", subject_id=artifact_id
                )
            )
    return EvaluationEvidence(configs, summaries, reports, contents, tuple(issues))
