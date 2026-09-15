from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.environments import (
    Command,
    DockerWorkspaceSpec,
    ExecutionResult,
    ExecutionStatus,
    WorkspaceCheckpoint,
)
from miniagent.runtime import (
    EvaluationCheck,
    EvaluationPlan,
    EvaluationRequest,
    EvaluationResult,
    NewRunEvent,
    NewRunSnapshot,
    RecoveryAction,
    RecoveryDecision,
    RecoveryLevel,
    RecoveryRequest,
    RecoveryTrigger,
    RunEvent,
    RunEventType,
    RunState,
    RuntimeConfig,
)


def run_state() -> RunState:
    now = datetime.now(UTC)
    return RunState(
        run_id=uuid4(),
        task="task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="Test.",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=now,
        updated_at=now,
    )


def test_snapshot_requires_matching_run() -> None:
    with pytest.raises(ValidationError, match="same run"):
        NewRunSnapshot(
            run_id=uuid4(),
            event_sequence=0,
            state=run_state(),
        )


def test_recovery_rejects_events_from_another_run() -> None:
    state = run_state()

    with pytest.raises(ValidationError, match="current run"):
        RecoveryRequest(
            state=state,
            trigger=RecoveryTrigger.TOOL_ERROR,
            recent_events=[
                RunEvent(
                    event_id=uuid4(),
                    run_id=uuid4(),
                    sequence=1,
                    occurred_at=datetime.now(UTC),
                    type=RunEventType.TOOL_COMPLETED,
                )
            ],
        )


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        (
            {
                "level": RecoveryLevel.L2,
                "action": RecoveryAction.RETRY,
                "reason": "temporary failure",
                "retry_delay_seconds": 1,
            },
            "invalid for level",
        ),
        (
            {
                "level": RecoveryLevel.L1,
                "action": RecoveryAction.RETRY,
                "reason": "temporary failure",
            },
            "requires a delay",
        ),
    ],
)
def test_retry_decision_requires_l1_and_delay(
    decision: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RecoveryDecision.model_validate(decision)


def test_rollback_decision_requires_checkpoint() -> None:
    state = run_state()
    checkpoint = WorkspaceCheckpoint(
        checkpoint_id="checkpoint-1",
        run_id=state.run_id,
        workspace_id="workspace-1",
        revision="abc123",
        created_at=datetime.now(UTC),
    )

    assert (
        RecoveryDecision(
            level=RecoveryLevel.L3,
            action=RecoveryAction.ROLLBACK,
            reason="tests regressed",
            checkpoint=checkpoint,
        ).checkpoint
        == checkpoint
    )


def test_successful_execution_requires_zero_exit_code() -> None:
    with pytest.raises(ValidationError, match="exit code 0"):
        ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            exit_code=1,
            duration_ms=10,
        )


def test_passed_evaluation_cannot_contain_failed_check() -> None:
    with pytest.raises(ValidationError, match="failed check"):
        EvaluationResult(
            passed=True,
            checks=[EvaluationCheck(name="target-tests", passed=False)],
        )


def test_evaluation_plan_must_match_run() -> None:
    with pytest.raises(ValidationError, match="evaluated run"):
        EvaluationRequest(
            run_id=uuid4(),
            task="task",
            candidate_output="candidate",
            plan=EvaluationPlan(
                workspace=DockerWorkspaceSpec(
                    run_id=uuid4(),
                    image="python:3.13",
                ),
                target_commands=[Command(argv=["pytest"])],
            ),
        )


def test_new_event_accepts_structured_payload() -> None:
    assert NewRunEvent(
        run_id=uuid4(),
        type=RunEventType.MODEL_RESPONDED,
        payload={"usage": {"input_tokens": 10}},
    ).payload["usage"] == {"input_tokens": 10}
