from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest

from miniagent.agents import AgentSpec
from miniagent.models.config import ModelTarget
from miniagent.models.protocol import ModelRequest
from miniagent.policy import (
    ApprovalRequest,
    PolicyDecision,
    PolicyEffect,
    PolicyRequest,
    RiskLevel,
    ShellExecution,
)
from miniagent.runtime import (
    NewRunEvent,
    RunEventType,
    RunState,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalRequested,
    ApprovalResolved,
    EventPayload,
    ModelRequested,
    ModelResponded,
    RunStarted,
    StepStarted,
)
from miniagent.runtime.store_contracts import (
    CreateApproval,
    RunCommit,
)
from miniagent.storage import SQLiteRunStore


def approval_event_id(
    run_id: UUID,
    approval_id: UUID,
    event_type: RunEventType,
) -> UUID:
    return uuid5(run_id, f"{approval_id}:{event_type.value}")


def make_event(
    *,
    run_id: UUID,
    type: RunEventType,
    payload: dict[str, object] | None = None,
    step_number: int | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> NewRunEvent:
    approval_id = UUID(str((payload or {}).get("approval_id", uuid4())))
    decision_id = UUID(str((payload or {}).get("decision_id", uuid4())))
    payloads: dict[RunEventType, EventPayload] = {
        RunEventType.RUN_STARTED: RunStarted(),
        RunEventType.MODEL_REQUESTED: ModelRequested(
            provider="test",
            model="test-model",
            request=ModelRequest(model="test-model", messages=()),
        ),
        RunEventType.MODEL_RESPONDED: ModelResponded(
            finish_reason="stop", tool_call_count=0
        ),
        RunEventType.STEP_STARTED: StepStarted(),
        RunEventType.APPROVAL_REQUESTED: ApprovalRequested(
            call_index=0,
            tool_call_id="call-1",
            tool_name="shell",
            approval_id=approval_id,
            decision_id=decision_id,
        ),
        RunEventType.APPROVAL_RESOLVED: ApprovalResolved(
            call_index=0,
            tool_call_id="call-1",
            tool_name="shell",
            approval_id=approval_id,
            outcome="approved",
        ),
        RunEventType.APPROVAL_INVALIDATED: ApprovalInvalidated(
            approval_id=approval_id,
            reason="authorization invalidated",
        ),
    }
    event_id = (
        approval_event_id(run_id, approval_id, type)
        if type
        in {
            RunEventType.APPROVAL_REQUESTED,
            RunEventType.APPROVAL_RESOLVED,
            RunEventType.APPROVAL_INVALIDATED,
        }
        else uuid4()
    )
    if type in {RunEventType.APPROVAL_REQUESTED, RunEventType.APPROVAL_RESOLVED}:
        step_number = 1
        span_id = span_id or "2222222222222222"
    return NewRunEvent(
        run_id=run_id,
        event_id=event_id,
        payload=payloads[type],
        step_number=step_number,
        span_id=span_id,
        parent_span_id=parent_span_id,
    )


def make_state() -> RunState:
    now = datetime.now(UTC)
    return create_run_state(
        run_id=uuid4(),
        task="fix the task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="Test.",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=now,
    )


def make_decision(state: RunState) -> PolicyDecision:
    digest = "a" * 64
    request = PolicyRequest(
        run_id=state.identity.run_id,
        step_number=1,
        call_index=0,
        tool_call_id="call-1",
        tool_name="shell",
        arguments={"command": "pytest"},
        operation=ShellExecution(command="pytest"),
        agent_spec_digest=digest,
        tool_contract_digest=digest,
        workspace_id="workspace-1",
        workspace_kind="local",
        execution_context_digest=digest,
    )
    return PolicyDecision(
        decision_id=uuid4(),
        request=request,
        request_digest=digest,
        policy_revision="policy-v1",
        effect=PolicyEffect.APPROVAL,
        risk=RiskLevel.HIGH,
        matched_rule_ids=("approval",),
        decisive_rule_ids=("approval",),
        reason_code="approval_required",
        reason="Approval is required.",
        input_summary="shell pytest",
        decided_at=datetime.now(UTC),
    )


async def create_approval(
    store: SQLiteRunStore,
    state: RunState,
    *,
    expires_at: datetime,
) -> tuple[ApprovalRequest, int]:
    decision = make_decision(state)
    request = ApprovalRequest(
        approval_id=uuid4(),
        decision_id=decision.decision_id,
        run_id=state.identity.run_id,
        request_digest=decision.request_digest,
        policy_revision=decision.policy_revision,
        requested_at=expires_at - timedelta(minutes=5),
        expires_at=expires_at,
    )
    result = await store.commit(
        RunCommit(
            state=state,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={
                        "approval_id": str(request.approval_id),
                        "decision_id": str(decision.decision_id),
                    },
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_id=approval_event_id(
                        state.identity.run_id,
                        request.approval_id,
                        RunEventType.APPROVAL_REQUESTED,
                    ),
                )
            ],
        )
    )
    return request, result.record.last_event_sequence


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteRunStore:
    result = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await result.initialize()
    return result
