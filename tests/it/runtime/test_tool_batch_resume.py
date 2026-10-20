import pytest

from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
)
from miniagent.tools.contracts import (
    ToolStatus,
)
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import AllowPolicyEngine, QueueProvider
from tests.support.runtime.tool_batch_resume import prefix


@pytest.mark.parametrize(
    ("steps", "allowed"), [(0, True), (1, True), (2, True), (3, False), (4, False)]
)
async def test_step_guard_limits_only_new_steps(tmp_path, steps, allowed):
    _store, execution, _tool = await prefix(tmp_path, ToolStatus.SUCCESS)
    execution.state = execution.state.model_copy(
        update={
            "execution": execution.state.execution.model_copy(
                update={"steps_started": steps}
            )
        }
    )
    assert (execution.before_next_step() is None) is allowed


@pytest.mark.parametrize(
    ("first_status", "suffix_status", "failed"),
    [
        (ToolStatus.ERROR, ToolStatus.SUCCESS, False),
        (ToolStatus.TIMEOUT, ToolStatus.SUCCESS, False),
        (ToolStatus.SUCCESS, ToolStatus.SUCCESS, False),
        (ToolStatus.SUCCESS, ToolStatus.ERROR, False),
        (ToolStatus.ERROR, ToolStatus.ERROR, True),
    ],
)
async def test_batch_failure_uses_earlier_persisted_results(
    tmp_path, first_status, suffix_status, failed
):
    store, execution, tool = await prefix(
        tmp_path, first_status, suffix_status=suffix_status
    )
    provider = QueueProvider([])
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
    )._drive(execution)
    assert (tmp_path / "effects").read_text().splitlines() == ["c0", "c1", "c2"]
    events = await store.list_events(result.run_id)
    completed = [
        event
        for event in events
        if event.payload.type == RunEventType.STEP_COMPLETED and event.step_number == 3
    ]
    assert len(completed) == 1 and completed[0].payload.failed is failed
    assert completed[0].payload.consecutive_failed_steps == (3 if failed else 0)
    assert provider.requests == []


async def test_finished_batch_at_step_limit_completes_without_new_request(tmp_path):
    store, execution, tool = await prefix(
        tmp_path, ToolStatus.SUCCESS, complete_batch=True
    )
    provider = QueueProvider([])
    await AgentRuntime(
        provider=provider, tools=ToolRegistry([tool]), run_store=store
    )._drive(execution)
    assert provider.requests == []
    assert (tmp_path / "effects").read_text().splitlines() == ["c0", "c1", "c2"]
