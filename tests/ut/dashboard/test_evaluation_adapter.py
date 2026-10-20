import json
from uuid import UUID

import pytest

from miniagent.dashboard.evaluation_evidence import (
    adapt_evaluation,
    read_recorded_report,
)
from miniagent.dashboard.json_content import ContentError
from tests.support.dashboard.builders import (
    change_evaluation_content,
    evaluation_example,
    report_document,
)

_BOOL_FIELDS = (
    "patch_exists",
    "patch_is_None",
    "patch_successfully_applied",
    "resolved",
    "infra_failure",
)


def test_report_native_booleans_and_optional_infra() -> None:
    document = report_document()
    assert (
        read_recorded_report(
            json.dumps(document).encode(), instance_id="org__repo-1"
        ).resolved
        is True
    )
    del document["org__repo-1"]["infra_failure"]
    assert (
        read_recorded_report(
            json.dumps(document).encode(), instance_id="org__repo-1"
        ).infra_failure
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [(field, value) for field in _BOOL_FIELDS for value in ("true", 1, None)],
)
def test_report_rejects_each_invalid_field_type(field: str, value) -> None:
    document = report_document()
    document["org__repo-1"][field] = value
    with pytest.raises(ContentError, match="report_fields_invalid"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")


@pytest.mark.parametrize(("field",), [(field,) for field in _BOOL_FIELDS[:-1]])
def test_required_report_boolean_cannot_default(field: str) -> None:
    document = report_document()
    del document["org__repo-1"][field]
    with pytest.raises(ContentError, match="report_fields_invalid"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("tests_status",), None),
        (("tests_status",), []),
        (("tests_status", "FAIL_TO_PASS"), None),
        (("tests_status", "PASS_TO_PASS"), []),
        *[
            (("tests_status", group, field), value)
            for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
            for field in ("success", "failure")
            for value in ("not a list", [1])
        ],
    ],
)
def test_report_container_shapes_are_strict(path, value) -> None:
    document = report_document()
    parent = document["org__repo-1"]
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ContentError, match="report_fields_invalid"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")


def test_adapter_only_uses_a_supported_recorded_harness() -> None:
    example = evaluation_example()
    adapted = adapt_evaluation(example.contents)
    assert UUID(int=104) in adapted.configs
    assert UUID(int=105) in adapted.summaries
    assert UUID(int=106) in adapted.reports
    config_content = example.contents[UUID(int=104)].content
    assert config_content is not None
    config = json.loads(config_content)
    config["harness_version"] = "unknown"
    changed = change_evaluation_content(example, 104, config)
    assert UUID(int=106) not in adapt_evaluation(changed.contents).reports


@pytest.mark.parametrize(
    ("path",),
    [
        (("tests_status",),),
        (("tests_status", "FAIL_TO_PASS"),),
        (("tests_status", "PASS_TO_PASS"),),
        *[
            (("tests_status", group, field),)
            for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
            for field in ("success", "failure")
        ],
    ],
)
def test_each_required_report_container_is_present(path) -> None:
    document = report_document()
    parent = document["org__repo-1"]
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(ContentError, match="report_fields_invalid"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")


@pytest.mark.parametrize(
    ("field", "value"),
    [(field, value) for field in _BOOL_FIELDS for value in (True, False)],
)
def test_native_bool_values_retain_their_recorded_meaning(
    field: str, value: bool
) -> None:
    document = report_document()
    document["org__repo-1"][field] = value
    assert (
        getattr(
            read_recorded_report(
                json.dumps(document).encode(), instance_id="org__repo-1"
            ),
            field,
        )
        is value
    )


@pytest.mark.parametrize(
    ("success", "failure"), [(["x", "x"], []), (["x"], ["x"]), ([], ["failed"])]
)
def test_report_rejects_internal_test_set_conflicts(success, failure) -> None:
    document = report_document()
    document["org__repo-1"]["tests_status"]["FAIL_TO_PASS"] = {
        "success": success,
        "failure": failure,
    }
    with pytest.raises(ContentError, match="report_tests_invalid"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")


def test_same_test_in_different_groups_is_not_a_duplicate() -> None:
    document = report_document()
    document["org__repo-1"]["tests_status"]["PASS_TO_PASS"]["success"] = [
        "tests/test_a.py::test_one"
    ]
    assert read_recorded_report(
        json.dumps(document).encode(), instance_id="org__repo-1"
    ).resolved


@pytest.mark.parametrize(
    ("document",), [({"different-instance": {}},), ({"org__repo-1": {}, "extra": {}},)]
)
def test_report_has_exactly_the_expected_instance(document) -> None:
    with pytest.raises(ContentError, match="report_instance_mismatch"):
        read_recorded_report(json.dumps(document).encode(), instance_id="org__repo-1")
