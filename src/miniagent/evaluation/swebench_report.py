import json

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError


class ReportValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class TestGroup(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="allow")

    success: list[str]
    failure: list[str]


class TestsStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    FAIL_TO_PASS: TestGroup
    PASS_TO_PASS: TestGroup


class InstanceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    patch_exists: StrictBool
    patch_is_None: StrictBool
    patch_successfully_applied: StrictBool
    resolved: StrictBool
    infra_failure: StrictBool = False
    tests_status: TestsStatus | None = None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReportValidationError(
                "REPORT_INVALID", f"Duplicate JSON field: {key}"
            )
        result[key] = value
    return result


def parse_instance_report(
    content: bytes,
    *,
    instance_id: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> InstanceReport:
    try:
        document = json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ReportValidationError("REPORT_INVALID", "Invalid report JSON") from exc
    if not isinstance(document, dict):
        raise ReportValidationError("REPORT_INVALID", "Report must be a JSON object")
    if set(document) != {instance_id}:
        raise ReportValidationError("REPORT_MISMATCH", "Report task does not match")
    try:
        report = InstanceReport.model_validate(document[instance_id])
    except ValidationError as exc:
        raise ReportValidationError("REPORT_INVALID", "Invalid report fields") from exc

    if report.resolved and (
        not report.patch_exists
        or report.patch_is_None
        or not report.patch_successfully_applied
        or report.infra_failure
        or report.tests_status is None
    ):
        raise ReportValidationError("REPORT_INVALID", "Resolved report lacks proof")
    if report.tests_status is not None:
        for group, required in (
            (report.tests_status.FAIL_TO_PASS, fail_to_pass),
            (report.tests_status.PASS_TO_PASS, pass_to_pass),
        ):
            success, failure = set(group.success), set(group.failure)
            if (
                len(success) != len(group.success)
                or len(failure) != len(group.failure)
                or success & failure
                or success | failure != set(required)
                or (report.resolved and failure)
            ):
                raise ReportValidationError("REPORT_INVALID", "Invalid test coverage")
    return report
