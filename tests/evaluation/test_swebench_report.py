import json

import pytest

from miniagent.evaluation.swebench_report import (
    ReportValidationError,
    parse_instance_report,
)

INSTANCE = "django__django-11477"


@pytest.fixture
def report():
    return {
        "patch_exists": True,
        "patch_is_None": False,
        "patch_successfully_applied": True,
        "resolved": True,
        "infra_failure": False,
        "tests_status": {
            "FAIL_TO_PASS": {"success": ["f"], "failure": []},
            "PASS_TO_PASS": {"success": ["p"], "failure": []},
        },
    }


def parse(report, *, fail_to_pass=None, pass_to_pass=None):
    return parse_instance_report(
        json.dumps({INSTANCE: report}).encode(),
        instance_id=INSTANCE,
        fail_to_pass=["f"] if fail_to_pass is None else fail_to_pass,
        pass_to_pass=["p"] if pass_to_pass is None else pass_to_pass,
    )


def test_complete_report_passes(report):
    assert parse(report).resolved is True


@pytest.mark.parametrize(("value",), [("true",), (1,)])
def test_string_resolved_is_invalid(report, value):
    report["resolved"] = value
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


@pytest.mark.parametrize(
    ("field",),
    [
        ("resolved",),
        ("patch_exists",),
        ("patch_is_None",),
        ("patch_successfully_applied",),
    ],
)
def test_required_boolean_cannot_be_missing(report, field):
    del report[field]
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


def test_optional_infra_failure_can_be_missing(report):
    del report["infra_failure"]
    assert parse(report).resolved is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("patch_exists", False),
        ("patch_is_None", True),
        ("patch_successfully_applied", False),
        ("infra_failure", True),
    ],
)
def test_resolved_cannot_hide_patch_or_infrastructure_failure(report, field, value):
    report[field] = value
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


@pytest.mark.parametrize(
    ("success", "failure"),
    [([], []), (["f", "f"], []), (["f", "extra"], []), (["f"], ["f"]), ([], ["f"])],
)
def test_resolved_requires_exact_successful_test_coverage(report, success, failure):
    report["tests_status"]["FAIL_TO_PASS"] = {"success": success, "failure": failure}
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


@pytest.mark.parametrize(("missing",), [("FAIL_TO_PASS",), ("PASS_TO_PASS",)])
def test_required_test_group_cannot_be_missing(report, missing):
    del report["tests_status"][missing]
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


def test_failure_list_cannot_be_missing(report):
    del report["tests_status"]["FAIL_TO_PASS"]["failure"]
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse(report)


def test_empty_rules_require_empty_group_evidence(report):
    for group in report["tests_status"].values():
        group["success"] = []
    assert parse(report, fail_to_pass=[], pass_to_pass=[]).resolved is True


def test_overlap_between_rule_groups_is_allowed(report):
    report["tests_status"]["PASS_TO_PASS"]["success"] = ["f"]
    assert parse(report, pass_to_pass=["f"]).resolved is True


def test_extra_unconsumed_upstream_fields_are_allowed(report):
    report["upstream"] = "future"
    report["tests_status"]["FAIL_TO_FAIL"] = {"future": "value"}
    assert parse(report).resolved is True


def test_unresolved_with_failed_tests_is_candidate_failure(report):
    report["resolved"] = False
    report["tests_status"]["FAIL_TO_PASS"] = {"success": [], "failure": ["f"]}
    assert parse(report).resolved is False


def test_unresolved_without_test_output_is_accepted_as_failure(report):
    report["resolved"] = False
    report["patch_successfully_applied"] = False
    del report["tests_status"]
    assert parse(report).resolved is False


@pytest.mark.parametrize(("content",), [(b"not json",), (b"[]",), (b"null",)])
def test_malformed_report_is_invalid(content):
    with pytest.raises(ReportValidationError, match="REPORT_INVALID"):
        parse_instance_report(
            content, instance_id=INSTANCE, fail_to_pass=["f"], pass_to_pass=["p"]
        )


@pytest.mark.parametrize(("extra",), [(False,), (True,)])
def test_wrong_or_extra_task_is_mismatch(report, extra):
    content = {"other": report}
    if extra:
        content[INSTANCE] = report
    with pytest.raises(ReportValidationError, match="REPORT_MISMATCH"):
        parse_instance_report(
            json.dumps(content).encode(),
            instance_id=INSTANCE,
            fail_to_pass=["f"],
            pass_to_pass=["p"],
        )
