from datetime import UTC, datetime
from uuid import uuid4

import pytest

from miniagent.environments.contracts import ExecutionResult, ExecutionStatus
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyEffect,
    PolicyEvaluation,
    RiskLevel,
)
from miniagent.runtime import AgentRuntime, RunStatus, RuntimeConfig, create_run_state
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.tools.contracts import ToolStatus
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool
from tests.resume_support import waiting_case
from tests.runtime.test_runtime import QueueProvider, agent
from tests.tools.test_contracts import EvidenceWorkspace


@pytest.mark.parametrize(
    ("effect", "interactive"),
    [
        (PolicyEffect.ALLOW, False),
        (PolicyEffect.DENY, False),
        (PolicyEffect.APPROVAL, False),
        (PolicyEffect.APPROVAL, True),
    ],
)
async def test_old_approval_is_not_reused_for_current_policy(
    tmp_path, effect, interactive
):
    spec = agent(allowed_tools=["shell"])
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=spec,
        config=RuntimeConfig(max_steps=1),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    store, execution, request = await waiting_case(
        tmp_path / "runs.sqlite3", state=state
    )
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="reviewer",
        reason="original human decision",
        resolved_at=datetime.now(UTC),
    )
    await execution.resolve_approval(resolution)
    record = await store.get(request.run_id)
    approvals = await store.list_unconsumed_approvals(request.run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), approvals)
    )

    class CurrentPolicy:
        revision = "changed-policy"
        calls = 0

        def evaluate(self, request):
            self.calls += 1
            return PolicyEvaluation(
                effect=effect,
                risk=RiskLevel.HIGH,
                matched_rule_ids=("current",),
                decisive_rule_ids=("current",),
                reason_code="CURRENT_POLICY",
                reason="current policy decision",
            )

    policy = CurrentPolicy()
    tool = ShellTool(
        EvidenceWorkspace(
            ExecutionResult(status=ExecutionStatus.SUCCESS, exit_code=0, duration_ms=0)
        )
    )

    class FreshHandler:
        def __init__(self) -> None:
            self.requests: list[ApprovalRequest] = []

        async def resolve(self, request, decision):
            self.requests.append(request)
            return ApprovalResolution(
                approval_id=request.approval_id,
                outcome=ApprovalOutcome.APPROVED,
                actor_kind="human",
                actor_id="new-reviewer",
                reason="fresh approval",
                resolved_at=datetime.now(UTC),
            )

    handler = FreshHandler()
    runtime = AgentRuntime(
        provider=QueueProvider([]),
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=policy,
        approval_handler=handler if interactive else None,
    )
    result = await runtime._drive(execution)
    assert policy.calls > 0
    assert (
        await store.get_approval(request.approval_id)
    ).approval.resolution == resolution
    if interactive:
        assert len(handler.requests) == 1
        assert handler.requests[0].approval_id != request.approval_id
    if effect is not PolicyEffect.APPROVAL or interactive:
        facts = await store.list_tool_results(result.run_id, step_number=1)
        assert facts[0].result.status is (
            ToolStatus.DENIED if effect is PolicyEffect.DENY else ToolStatus.SUCCESS
        )
    if effect is PolicyEffect.APPROVAL and not interactive:
        assert result.status is RunStatus.FAILED
        assert result.error is not None and result.error.code == "APPROVAL_UNAVAILABLE"
