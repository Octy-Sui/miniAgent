from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from miniagent.environments.managed_execution import HostProcessIdentity
from miniagent.runtime.progress import (
    CandidateDisposition,
    CurrentStep,
    OperationRef,
    RequestProgress,
    ResponseDisposition,
    TerminationDecision,
)
from miniagent.runtime.result import TerminationReason


def tool_operation(**updates):
    return {
        "kind": "tool",
        "attempt_id": uuid4(),
        "started_event_id": uuid4(),
        "started_sequence": 1,
        "step_number": 1,
        "call_index": 1,
        "tool_call_id": "c1",
    } | updates


def test_operation_round_trip():
    """TC-U5: tool scope survives persistence without losing its call cursor."""
    operation = OperationRef(**tool_operation())
    assert OperationRef.model_validate_json(operation.model_dump_json()) == operation
    assert operation.call_index == 1


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"tool_call_id": None}, "tool_call_id"),
        ({"call_index": None}, "call_index"),
        ({"step_number": None}, "step_number"),
        ({"started_sequence": 0}, "started_sequence"),
        (
            {
                "kind": "model",
                "call_index": None,
                "tool_call_id": None,
                "step_number": None,
            },
            "step_number",
        ),
    ],
)
def test_operation_rejects_incomplete_scope(updates, match):
    with pytest.raises(ValidationError, match=match):
        OperationRef(**tool_operation(**updates))


@pytest.mark.parametrize(
    ("field",), [("business_retry_used",), ("summary_retry_used",)]
)
def test_negative_retry_rejected(field):
    with pytest.raises(ValidationError, match=field):
        RequestProgress.model_validate({field: -1})


def test_business_retry_does_not_exceed_one():
    with pytest.raises(ValidationError, match="business_retry_used"):
        RequestProgress(business_retry_used=2)


def test_response_disposition_requires_candidate_or_decision():
    candidate = CandidateDisposition(text="resume-local-ok")
    assert candidate.kind == "candidate" and candidate.text == "resume-local-ok"
    assert RequestProgress().business_retry_used == 0
    with pytest.raises(ValidationError, match="text"):
        CandidateDisposition(text="")
    with pytest.raises(ValidationError, match="decision"):
        TypeAdapter(ResponseDisposition).validate_python({"kind": "terminate"})


def test_successful_termination_requires_output():
    with pytest.raises(ValidationError, match="final_output"):
        TerminationDecision(status="succeeded", reason=TerminationReason.TASK_VERIFIED)


def test_step_requires_positive_sequence():
    with pytest.raises(ValidationError, match="started_sequence"):
        CurrentStep(
            step_number=1,
            message_start=0,
            started_event_id=uuid4(),
            started_sequence=-1,
        )


def test_execution_identities_require_same_attempt_and_unique_id():
    operation = tool_operation()
    identity = HostProcessIdentity(
        role="local_command",
        attempt_id=operation["attempt_id"],
        execution_id=uuid4(),
        pid=123,
        create_time=1.0,
        host_boot_id="boot",
        pgid=123,
        sid=123,
    )
    with pytest.raises(ValidationError, match="execution_id"):
        OperationRef(**operation, executions=(identity, identity))
    with pytest.raises(ValidationError, match="attempt_id"):
        OperationRef(
            **operation,
            executions=(identity.model_copy(update={"attempt_id": uuid4()}),),
        )
