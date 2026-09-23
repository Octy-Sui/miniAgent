from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from miniagent.agents import AgentSpec
from miniagent.environments import (
    Command,
    ExecutionResult,
    ExecutionStatus,
    WorkspaceCheckpoint,
)
from miniagent.evaluation import CandidateOutput
from miniagent.models.config import ModelTarget
from miniagent.models.messages import AssistantMessage, Message, ToolMessage
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
    RunTrace,
    SpanCategory,
    TraceSpan,
    TraceSpanStatus,
    create_run_state,
)
from miniagent.runtime.events import (
    ModelRequested,
    ModelResponded,
    StepStarted,
    ToolExecutionEnded,
)
from miniagent.tools.contracts import ToolCall


def run_state() -> RunState:
    now = datetime.now(UTC)
    return create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="Test.",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=now,
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
                    payload=ToolExecutionEnded(
                        call_index=0,
                        tool_call_id="call-1",
                        tool_name="shell",
                        status="error",
                    ),
                    step_number=1,
                    span_id="1111111111111111",
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
        run_id=state.identity.run_id,
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


def test_message_union_enforces_role_specific_fields() -> None:
    adapter = TypeAdapter(Message)

    assert adapter.validate_python(
        {
            "role": "assistant",
            "tool_calls": [{"id": "call", "name": "tool", "arguments": {}}],
        }
    ) == AssistantMessage(tool_calls=(ToolCall(id="call", name="tool", arguments={}),))

    with pytest.raises(ValidationError, match="tool_call_id"):
        adapter.validate_python({"role": "tool", "content": "result"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        adapter.validate_python(
            {"role": "user", "content": "task", "tool_call_id": "call"}
        )


def test_tool_message_requires_nonempty_correlation_id() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        ToolMessage(content="result", tool_call_id="")


def test_passed_evaluation_cannot_contain_failed_check() -> None:
    with pytest.raises(ValidationError, match="failed check"):
        EvaluationResult(
            passed=True,
            checks=[EvaluationCheck(name="target-tests", passed=False)],
        )


def test_evaluation_request_keeps_run_scope_without_workspace_config() -> None:
    run_id = uuid4()

    request = EvaluationRequest(
        run_id=run_id,
        task="task",
        candidate=CandidateOutput(text="candidate"),
        plan=EvaluationPlan(target_commands=[Command(argv=["pytest"])]),
    )

    assert request.run_id == run_id
    assert request.plan is not None
    assert request.plan.target_commands == [Command(argv=["pytest"])]


def test_new_event_accepts_structured_payload() -> None:
    event = NewRunEvent(
        run_id=uuid4(),
        payload=ModelResponded(finish_reason="stop", tool_call_count=0),
    )

    assert event.type is RunEventType.MODEL_RESPONDED
    assert event.payload == ModelResponded(finish_reason="stop", tool_call_count=0)


def test_event_payload_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError, match="finish_reason"):
        NewRunEvent.model_validate(
            {
                "run_id": uuid4(),
                "payload": {
                    "type": RunEventType.MODEL_RESPONDED,
                    "tool_call_count": 0,
                },
            }
        )


def test_step_event_requires_number_and_span_scope_is_validated() -> None:
    with pytest.raises(ValidationError, match="step number"):
        NewRunEvent(run_id=uuid4(), payload=StepStarted())

    with pytest.raises(ValidationError, match="current span"):
        NewRunEvent(
            run_id=uuid4(),
            payload=ModelRequested(provider="test", model="test-model"),
            parent_span_id="2222222222222222",
        )


def test_run_trace_uses_run_id_as_trace_id() -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    trace = RunTrace(
        run_id=run_id,
        spans=[
            TraceSpan(
                span_id="1111111111111111",
                category=SpanCategory.RUN,
                name="miniagent.run",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=1,
                started_at=now,
            ),
            TraceSpan(
                span_id="2222222222222222",
                parent_span_id="1111111111111111",
                category=SpanCategory.STEP,
                name="miniagent.step",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=2,
                started_at=now,
                step_number=1,
            ),
            TraceSpan(
                span_id="3333333333333333",
                parent_span_id="2222222222222222",
                category=SpanCategory.MODEL,
                name="miniagent.model.generate",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=3,
                started_at=now,
            ),
            TraceSpan(
                span_id="4444444444444444",
                parent_span_id="2222222222222222",
                category=SpanCategory.TOOL,
                name="miniagent.tool",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=4,
                started_at=now,
            ),
            TraceSpan(
                span_id="5555555555555555",
                parent_span_id="4444444444444444",
                category=SpanCategory.APPROVAL_WAIT,
                name="miniagent.approval.wait",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=5,
                started_at=now,
            ),
            TraceSpan(
                span_id="6666666666666666",
                parent_span_id="4444444444444444",
                category=SpanCategory.TOOL_EXECUTION,
                name="miniagent.tool.execute",
                status=TraceSpanStatus.IN_PROGRESS,
                start_sequence=6,
                started_at=now,
            ),
        ],
    )

    assert trace.trace_id == run_id.hex


def test_run_trace_rejects_invalid_span_hierarchy() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError, match="tool span cannot be a child of run"):
        RunTrace(
            run_id=uuid4(),
            spans=[
                TraceSpan(
                    span_id="1111111111111111",
                    category=SpanCategory.RUN,
                    name="miniagent.run",
                    status=TraceSpanStatus.IN_PROGRESS,
                    start_sequence=1,
                    started_at=now,
                ),
                TraceSpan(
                    span_id="2222222222222222",
                    parent_span_id="1111111111111111",
                    category=SpanCategory.TOOL,
                    name="miniagent.tool.execute",
                    status=TraceSpanStatus.IN_PROGRESS,
                    start_sequence=2,
                    started_at=now,
                ),
            ],
        )


def test_run_trace_preserves_non_contained_span_timing() -> None:
    now = datetime.now(UTC)
    trace = RunTrace(
        run_id=uuid4(),
        spans=[
            TraceSpan(
                span_id="1111111111111111",
                category=SpanCategory.RUN,
                name="miniagent.run",
                status=TraceSpanStatus.OK,
                start_sequence=2,
                end_sequence=10,
                started_at=now + timedelta(seconds=1),
                ended_at=now + timedelta(seconds=2),
            ),
            TraceSpan(
                span_id="2222222222222222",
                parent_span_id="1111111111111111",
                category=SpanCategory.STEP,
                name="miniagent.step",
                status=TraceSpanStatus.OK,
                start_sequence=1,
                end_sequence=11,
                started_at=now,
                ended_at=now + timedelta(seconds=3),
                step_number=1,
            ),
        ],
    )

    assert trace.spans[1].end_sequence == 11
