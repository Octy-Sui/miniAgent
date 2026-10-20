from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.agents import AgentSpec
from miniagent.models.config import ModelTarget
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.progress import ExecutionPhase, InterruptionInfo
from miniagent.runtime.result import TerminationReason
from miniagent.runtime.state import (
    InvalidRunTransition,
    RunState,
    RunStatus,
    create_run_state,
)
from miniagent.runtime.transitions import begin_resume, complete_resume, finish, suspend


def run_state() -> RunState:
    return create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="test",
            model=ModelTarget(provider="test", model="test"),
        ),
        config=RuntimeConfig(),
    )


def batch_state() -> RunState:
    data = run_state().model_dump(mode="python")
    data["lifecycle"]["status"] = "running"
    data["execution"].update(
        phase="execute_tools",
        cursor="execute_tool_calls",
        steps_started=1,
        current_step={
            "step_number": 1,
            "message_start": 0,
            "started_event_id": uuid4(),
            "started_sequence": 1,
        },
        response_disposition={"kind": "tools"},
        tool_batch={
            "calls": [
                {"id": f"c{i}", "name": "shell", "arguments": {}} for i in range(3)
            ],
            "next_index": 1,
        },
    )
    return RunState.model_validate(data)


def interruption() -> InterruptionInfo:
    return InterruptionInfo(signal_number=2, requested_at=datetime.now(UTC))


def test_resume_keeps_original_business_source():
    """TC-U3-06/07/08: interrupted recovery never replaces the business origin."""
    state = begin_resume(batch_state(), attempt_id=uuid4(), source_sequence=5)
    state = suspend(state, interruption())
    state = begin_resume(state, attempt_id=uuid4(), source_sequence=7)
    recovered = complete_resume(state)
    assert recovered.lifecycle.status is RunStatus.RUNNING
    assert recovered.execution.phase is ExecutionPhase.EXECUTE_TOOLS
    assert recovered.recovery.resume_attempts == 2
    assert recovered.approval.pending is None
    assert recovered.lifecycle.finished_at is None
    assert recovered.lifecycle.termination_reason is None
    assert recovered.execution.tool_batch is not None
    assert recovered.execution.tool_batch.next_index == 1
    assert recovered.recovery.session is None
    assert recovered.recovery.suspension is None


@pytest.mark.parametrize(("via_suspend",), [(True,), (False,)])
def test_waiting_approval_pause_clears_pending(via_suspend):
    """TC-U3-02: only WAITING_APPROVAL may retain pending authorization."""
    data = batch_state().model_dump(mode="python")
    data["lifecycle"]["status"] = "waiting_approval"
    data["execution"]["cursor"] = None
    data["approval"]["pending"] = {
        "approval_id": uuid4(),
        "step_number": 1,
        "call_index": 1,
        "tool_call_id": "c1",
    }
    state = RunState.model_validate(data)
    if via_suspend:
        state = suspend(state, interruption())
        assert state.approval.pending is None
    state = complete_resume(begin_resume(state, attempt_id=uuid4(), source_sequence=5))
    assert state.lifecycle.status is RunStatus.RUNNING
    assert state.approval.pending is None
    assert state.execution.tool_batch is not None
    assert state.execution.tool_batch.next_index == 1


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("created", "preparing"),
        ("preparing", "preparing"),
        ("running", "running"),
        ("waiting_approval", "running"),
        ("evaluating", "evaluating"),
    ],
)
def test_all_business_origins_survive_recovery(origin, expected):
    """TC-U3-01/03/04/07: recovery preserves all five business origins."""
    data = batch_state().model_dump(mode="python")
    if origin in ("created", "preparing"):
        data = run_state().model_dump(mode="python")
    elif origin == "evaluating":
        data["lifecycle"]["candidate_output"] = "candidate"
        data["execution"].update(
            phase="evaluate_candidate",
            cursor=None,
            current_step=None,
            tool_batch=None,
            response_disposition={"kind": "candidate", "text": "candidate"},
        )
    elif origin == "waiting_approval":
        data["approval"]["pending"] = {
            "approval_id": uuid4(),
            "step_number": 1,
            "call_index": 1,
            "tool_call_id": "c1",
        }
    data["lifecycle"]["status"] = origin
    if origin != "running":
        data["execution"]["cursor"] = None
    state = suspend(RunState.model_validate(data), interruption())
    state = begin_resume(state, attempt_id=uuid4(), source_sequence=5)
    state = begin_resume(state, attempt_id=uuid4(), source_sequence=6)
    assert complete_resume(state).lifecycle.status.value == expected


@pytest.mark.parametrize(
    ("variant",), [(f"E{i}",) for i in range(1, 13)] + [("terminal_operation",)]
)
def test_invalid_phase_projection(variant):
    """TC-U5-02..13: independently reject inconsistent persisted progress."""
    data = batch_state().model_dump(mode="python")
    execution = data["execution"]
    match = ""
    if variant == "E1":
        execution["phase"] = "request_model"
        match = "cursor"
    elif variant == "E2":
        data["lifecycle"]["status"] = "preparing"
        match = "cursor"
    elif variant == "E3":
        execution["tool_batch"] = None
        match = "batch"
    elif variant == "E4":
        execution.update(phase="complete_step", cursor="execute_tool_calls")
        match = "complete_step"
    elif variant == "E5":
        data["lifecycle"]["status"] = "waiting_approval"
        execution["cursor"] = None
        match = "[Pp]ending"
    elif variant == "E6":
        data = suspend(batch_state(), interruption()).model_dump(mode="python")
        data["approval"]["pending"] = {
            "approval_id": uuid4(),
            "step_number": 1,
            "call_index": 1,
            "tool_call_id": "c1",
        }
        match = "[Pp]ending"
    elif variant == "E7":
        execution["active_operation"] = {
            "kind": "tool",
            "attempt_id": uuid4(),
            "started_event_id": uuid4(),
            "started_sequence": 1,
            "step_number": 1,
            "call_index": 2,
            "tool_call_id": "c2",
        }
        match = "operation"
    elif variant == "E8":
        execution["response_disposition"] = {"kind": "candidate"}
        match = "text"
    elif variant == "E9":
        execution.update(
            phase="terminate",
            cursor="request_model",
            tool_batch=None,
            response_disposition=None,
        )
        match = "decision"
    elif variant == "E10":
        data["conversation"]["completed_steps"] = [
            {"step_number": 1, "message_start": 0, "message_end": 1}
        ]
        match = "current_step"
    elif variant in ("E11", "terminal_operation"):
        data = finish(
            batch_state(), RunStatus.FAILED, TerminationReason.MODEL_FAILED
        ).model_dump(mode="python")
        if variant == "E11":
            data["execution"]["phase"] = "prepare"
            match = "phase"
        else:
            data["execution"]["active_operation"] = {
                "kind": "summary",
                "attempt_id": uuid4(),
                "started_event_id": uuid4(),
                "started_sequence": 1,
            }
            match = "operation"
    elif variant == "E12":
        data["lifecycle"]["status"] = "recovering"
        execution["cursor"] = None
        match = "session"
    with pytest.raises(ValidationError, match=match):
        RunState.model_validate(data)


@pytest.mark.parametrize(
    ("index", "valid"),
    [
        (-2, False),
        (-1, False),
        (0, True),
        (1, True),
        (2, True),
        (3, True),
        (4, False),
        (5, False),
    ],
)
def test_batch_cursor_boundaries(index, valid):
    """TC-U8: cursor exactly n belongs to complete_step, never execute_tools."""
    data = batch_state().model_dump(mode="python")
    data["execution"]["tool_batch"]["next_index"] = index
    if index == 3:
        data["execution"].update(phase="complete_step", cursor="execute_tool_calls")
    if valid:
        batch = RunState.model_validate(data).execution.tool_batch
        assert batch is not None and batch.next_index == index
    else:
        with pytest.raises(ValidationError, match="next_index|cursor"):
            RunState.model_validate(data)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (RunStatus.FAILED, TerminationReason.MODEL_FAILED),
        (RunStatus.CANCELLED, TerminationReason.CANCEL_REQUESTED),
        (RunStatus.SUCCEEDED, TerminationReason.TASK_VERIFIED),
    ],
)
def test_terminal_states_have_no_recovery_edges(status, reason):
    """TC-U3-09..11: terminal states never reopen."""
    from miniagent.runtime.transitions import begin_evaluation

    state = begin_evaluation(batch_state(), "candidate")
    state = finish(
        state,
        status,
        reason,
        final_output="done" if status is RunStatus.SUCCEEDED else None,
    )
    with pytest.raises(InvalidRunTransition):
        suspend(state, interruption())
    with pytest.raises(InvalidRunTransition):
        begin_resume(state, attempt_id=uuid4(), source_sequence=9)


def test_summary_retry_uses_frozen_limit():
    data = batch_state().model_dump(mode="python")
    data["execution"]["request_progress"] = {"summary_retry_used": 2}
    with pytest.raises(ValidationError, match="summary_retry_used"):
        RunState.model_validate(data)
