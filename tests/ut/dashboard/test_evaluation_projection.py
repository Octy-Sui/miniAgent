import json
from dataclasses import replace
from hashlib import sha256
from uuid import UUID

import pytest

from miniagent.dashboard.evaluation_evidence import adapt_evaluation
from miniagent.dashboard.projections.evaluation import project_evaluations
from miniagent.dashboard.reads import (
    ManifestItem,
    ReadIssue,
    StoredEvent,
    VerifiedContent,
)
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import EvaluationCompleted, RunEvent, ToolEnded
from miniagent.runtime.result import TerminationReason
from miniagent.runtime.state import RunStatus
from miniagent.tools.contracts import ToolResult
from tests.support.dashboard.builders import (
    EvaluationExample,
    change_evaluation_content,
    evaluation_example,
)


def one(example: EvaluationExample):
    views = project_evaluations(
        example.run,
        example.boundary,
        example.events,
        example.manifests,
        adapt_evaluation(example.contents),
    )
    assert len(views) == 1
    return views[0]


def without(example: EvaluationExample, number: int) -> EvaluationExample:
    return replace(
        example,
        contents={
            key: value
            for key, value in example.contents.items()
            if key != UUID(int=number)
        },
        manifests=tuple(
            item for item in example.manifests if item.artifact_id != UUID(int=number)
        ),
    )


def test_recorded_pass_requires_the_whole_evidence_chain() -> None:
    view = one(evaluation_example())
    assert view.outcome == "recorded_pass"
    assert "已记录独立评测通过" in [field.value for field in view.fields]
    assert "目标测试清单未持久化，无法复核完整覆盖" in [
        field.value for field in view.fields
    ]
    assert any("报告记录的测试" in field.label for field in view.fields)


@pytest.mark.parametrize(("number",), [(103,), (104,), (105,), (106,)])
def test_missing_each_necessary_artifact_is_incomplete(number: int) -> None:
    assert one(without(evaluation_example(), number)).outcome == "incomplete"


@pytest.mark.parametrize(("number",), [(107,), (108,), (109,), (110,)])
def test_missing_logs_do_not_replace_verdict(number: int) -> None:
    view = one(without(evaluation_example(), number))
    assert view.outcome == "recorded_pass"
    assert "log_missing" in {issue.code for issue in view.issues}


@pytest.mark.parametrize(
    ("number", "field", "replacement"),
    [
        (104, "run_id", str(UUID(int=999))),
        (104, "attempt_id", str(UUID(int=999))),
        (104, "instance_id", "other-instance"),
        (105, "run_id", str(UUID(int=999))),
        (105, "attempt_id", str(UUID(int=999))),
        (105, "instance_id", "other-instance"),
        (104, "patch_sha256", "0" * 64),
        (105, "patch_sha256", "0" * 64),
        (104, "submission_id", str(UUID(int=999))),
        (105, "submission_id", str(UUID(int=999))),
    ],
)
def test_explicit_identity_mismatches_are_conflicts(
    number: int, field: str, replacement: str
) -> None:
    example = evaluation_example()
    content = example.contents[UUID(int=number)].content
    assert content is not None
    document = json.loads(content)
    document[field] = replacement
    assert (
        one(change_evaluation_content(example, number, document)).outcome == "conflict"
    )


def test_missing_and_conflicting_facts_both_remain_visible() -> None:
    example = evaluation_example()
    content = example.contents[UUID(int=104)].content
    assert content is not None
    config = json.loads(content)
    config["attempt_id"] = str(UUID(int=999))
    view = one(without(change_evaluation_content(example, 104, config), 106))
    assert view.outcome == "conflict"
    assert {"evidence_conflict", "evidence_incomplete"} <= {
        issue.code for issue in view.issues
    }


def test_missing_event_cannot_be_replaced_by_report_or_logs() -> None:
    assert one(replace(evaluation_example(), events=())).outcome == "incomplete"


def test_unverified_patch_content_is_not_pass_evidence() -> None:
    example = evaluation_example()
    contents = dict(example.contents)
    contents[UUID(int=103)] = replace(
        contents[UUID(int=103)],
        content=None,
        issues=(ReadIssue("read_limit_exceeded", "超限"),),
    )
    assert one(replace(example, contents=contents)).outcome == "incomplete"


def test_local_workspace_does_not_get_a_swebench_card() -> None:
    example = evaluation_example()
    assert (
        project_evaluations(
            replace(example.run, workspace_kind="local"),
            example.boundary,
            example.events,
            example.manifests,
            adapt_evaluation(example.contents),
        )
        == ()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("resolved", False),
        ("patch_exists", False),
        ("patch_is_None", True),
        ("patch_successfully_applied", False),
        ("infra_failure", True),
    ],
)
def test_each_report_negative_fact_blocks_recorded_pass(
    field: str, value: bool
) -> None:
    example = evaluation_example()
    content = example.contents[UUID(int=106)].content
    assert content is not None
    document = json.loads(content)
    document["org__repo-1"][field] = value
    assert one(change_evaluation_content(example, 106, document)).outcome == "conflict"


def test_runtime_failure_is_independent_of_the_report() -> None:
    example = evaluation_example()
    assert example.run.record is not None
    state = example.run.record.state
    state = state.model_copy(
        update={
            "lifecycle": state.lifecycle.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "termination_reason": TerminationReason.EVALUATION_FAILED,
                    "final_output": None,
                }
            )
        }
    )
    state = type(state).model_validate(state.model_dump())
    run = replace(
        example.run,
        envelope=replace(example.run.envelope, status=RunStatus.FAILED),
        record=example.run.record.model_copy(update={"state": state}),
    )
    view = one(
        replace(
            example,
            run=run,
            boundary=example.boundary.model_copy(
                update={"run_status": RunStatus.FAILED}
            ),
        )
    )
    assert view.outcome == "conflict"


def test_v3_result_cannot_disagree_with_event_passed() -> None:
    example = evaluation_example()
    item = example.events[0]
    assert item.event is not None and isinstance(
        item.event.payload, EvaluationCompleted
    )
    result = item.event.payload.result
    assert result is not None
    event = item.event.model_copy(
        update={
            "payload": item.event.payload.model_copy(
                update={"result": result.model_copy(update={"passed": False})}
            )
        }
    )
    assert (
        one(replace(example, events=(replace(item, event=event),))).outcome
        == "conflict"
    )


def test_unreferenced_unknown_summary_does_not_replace_the_named_final() -> None:
    example = evaluation_example()
    original = example.contents[UUID(int=105)]
    content = b'{"unrecognized_shape":"preview-only"}'
    digest = sha256(content).hexdigest()
    record = original.record.model_copy(
        update={
            "artifact_id": UUID(int=900),
            "sha256": digest,
            "size_bytes": len(content),
            "uri": "file:///fixture/content/" + digest[:2] + "/" + digest,
        }
    )
    extra = VerifiedContent(record, content, original.read_at, ())
    changed = replace(
        example,
        contents={**example.contents, record.artifact_id: extra},
        manifests=(*example.manifests, ManifestItem(record.artifact_id, record, ())),
    )
    assert one(changed).outcome == "recorded_pass"


@pytest.mark.parametrize(("attempt_value",), [(None,), ("invalid-uuid",)])
def test_invalid_metadata_attempt_is_never_filled_from_content(attempt_value) -> None:
    example = evaluation_example()
    original = example.contents[UUID(int=104)]
    metadata = dict(original.record.metadata)
    if attempt_value is None:
        del metadata["attempt_id"]
    else:
        metadata["attempt_id"] = attempt_value
    record = original.record.model_copy(update={"metadata": metadata})
    changed = replace(
        example,
        contents={
            **example.contents,
            record.artifact_id: replace(original, record=record),
        },
        manifests=tuple(
            ManifestItem(item.artifact_id, record, item.issues)
            if item.artifact_id == record.artifact_id
            else item
            for item in example.manifests
        ),
    )
    views = project_evaluations(
        changed.run,
        changed.boundary,
        changed.events,
        changed.manifests,
        adapt_evaluation(changed.contents),
    )
    assert any(view.attempt_id is None for view in views)
    assert (
        next(view for view in views if view.attempt_id == UUID(int=101)).outcome
        != "recorded_pass"
    )


def with_refs(example: EvaluationExample, refs) -> EvaluationExample:
    item = example.events[0]
    assert item.event is not None and isinstance(
        item.event.payload, EvaluationCompleted
    )
    result = item.event.payload.result
    assert result is not None
    payload = item.event.payload.model_copy(
        update={"result": result.model_copy(update={"artifacts": refs})}
    )
    return replace(
        example,
        events=(
            replace(
                item,
                event=item.event.model_copy(
                    update={"artifacts": refs, "payload": payload}
                ),
            ),
        ),
    )


def test_event_passed_false_is_a_separate_negative_fact() -> None:
    example = evaluation_example()
    item = example.events[0]
    assert item.event is not None and isinstance(
        item.event.payload, EvaluationCompleted
    )
    result = item.event.payload.result
    assert result is not None
    payload = item.event.payload.model_copy(
        update={"passed": False, "result": result.model_copy(update={"passed": False})}
    )
    assert (
        one(
            replace(
                example,
                events=(
                    replace(
                        item, event=item.event.model_copy(update={"payload": payload})
                    ),
                ),
            )
        ).outcome
        == "conflict"
    )


@pytest.mark.parametrize(("sha",), [(None,), ("0" * 64,)])
def test_reference_hash_missing_differs_from_recorded_mismatch(sha) -> None:
    example = evaluation_example()
    item = example.events[0]
    assert item.event is not None
    refs = [
        ref.model_copy(update={"sha256": sha})
        if ref.artifact_id == UUID(int=106)
        else ref
        for ref in item.event.artifacts
    ]
    assert one(with_refs(example, refs)).outcome == (
        "incomplete" if sha is None else "conflict"
    )


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("collecting", "incomplete"), ("failed", "conflict"), ("cancelled", "conflict")],
)
def test_summary_verdict_is_not_implicitly_final(verdict: str, expected: str) -> None:
    example = evaluation_example()
    content = example.contents[UUID(int=105)].content
    assert content is not None
    summary = json.loads(content)
    summary["verdict"] = verdict
    assert one(change_evaluation_content(example, 105, summary)).outcome == expected


def test_successful_shell_and_pass_log_do_not_replace_a_report() -> None:
    example = evaluation_example()
    example = change_evaluation_content(example, 107, {"stdout": "ALL PASSED"})
    example = without(example, 106)
    event = RunEvent(
        run_id=example.boundary.run_id,
        event_id=UUID(int=999),
        sequence=4,
        occurred_at=example.boundary.read_at,
        step_number=1,
        span_id="0000000000000001",
        payload=ToolEnded(
            call_index=0,
            tool_call_id="shell",
            tool_name="shell",
            status="success",
            result=ToolResult(
                tool_call_id="shell",
                status="success",
                content="ALL PASSED",
                data={"exit_code": 0},
            ),
        ),
    )
    shell = StoredEvent(
        event.event_id,
        event.run_id,
        event.sequence,
        str(event.type),
        event.occurred_at,
        1,
        event.span_id,
        None,
        event,
        (),
    )
    assert (
        one(replace(example, events=(*example.events, shell))).outcome == "incomplete"
    )


def test_two_complete_attempts_keep_their_own_evidence() -> None:
    example = evaluation_example()
    second = {}
    for identifier, original in example.contents.items():
        assert original.content is not None
        number = identifier.int + 1000
        metadata = dict(original.record.metadata)
        if original.record.type == "workspace_patch":
            metadata["submission_id"] = str(UUID(int=202))
            body = original.content
        else:
            metadata["attempt_id"] = str(UUID(int=201))
            body = original.content
            if original.record.type in ("evaluation_config", "evaluation_summary"):
                document = json.loads(body)
                document.update(
                    attempt_id=str(UUID(int=201)),
                    submission_id=str(UUID(int=202)),
                    patch_artifact_id=str(UUID(int=1103)),
                )
                if original.record.type == "evaluation_config":
                    document["patch_ref"] = (
                        second[UUID(int=1103)]
                        .record.reference()
                        .model_dump(mode="json")
                    )
                body = json.dumps(document).encode()
        digest = sha256(body).hexdigest()
        uri = original.record.uri.rsplit("/", 2)[0] + f"/{digest[:2]}/{digest}"
        record = original.record.model_copy(
            update={
                "artifact_id": UUID(int=number),
                "metadata": metadata,
                "sha256": digest,
                "size_bytes": len(body),
                "uri": uri,
            }
        )
        second[record.artifact_id] = VerifiedContent(record, body, original.read_at, ())
    first = example.events[0]
    assert first.event is not None
    refs = [content.record.reference() for content in second.values()]
    event = first.event.model_copy(
        update={
            "event_id": UUID(int=220),
            "sequence": 6,
            "artifacts": refs,
            "payload": EvaluationCompleted(
                passed=True,
                result=EvaluationResult(passed=True, output="done", artifacts=refs),
            ),
        }
    )
    second_event = replace(first, event_id=event.event_id, sequence=6, event=event)
    assert example.run.record is not None
    run = replace(
        example.run,
        envelope=replace(example.run.envelope, last_event_sequence=6),
        record=example.run.record.model_copy(update={"last_event_sequence": 6}),
    )
    changed = replace(
        example,
        run=run,
        boundary=example.boundary.model_copy(update={"as_of_sequence": 6}),
        events=(*example.events, second_event),
        manifests=(
            *example.manifests,
            *(ManifestItem(key, value.record, ()) for key, value in second.items()),
        ),
        contents={**example.contents, **second},
    )
    views = project_evaluations(
        changed.run,
        changed.boundary,
        changed.events,
        changed.manifests,
        adapt_evaluation(changed.contents),
    )
    assert [(view.attempt_id, view.outcome) for view in views] == [
        (UUID(int=101), "recorded_pass"),
        (UUID(int=201), "recorded_pass"),
    ]


def test_a_report_from_another_attempt_is_not_spliced_into_the_chain() -> None:
    example = evaluation_example()
    content = example.contents[UUID(int=106)]
    record = content.record.model_copy(
        update={
            "metadata": {**content.record.metadata, "attempt_id": str(UUID(int=201))}
        }
    )
    changed = replace(
        example,
        contents={
            **example.contents,
            record.artifact_id: replace(content, record=record),
        },
        manifests=tuple(
            ManifestItem(item.artifact_id, record, item.issues)
            if item.artifact_id == record.artifact_id
            else item
            for item in example.manifests
        ),
    )
    views = project_evaluations(
        changed.run,
        changed.boundary,
        changed.events,
        changed.manifests,
        adapt_evaluation(changed.contents),
    )
    assert {view.attempt_id for view in views} == {UUID(int=101), UUID(int=201)}
    assert all(view.outcome != "recorded_pass" for view in views)
