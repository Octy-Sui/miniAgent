from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import ToolsDisposition
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import (
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolStatus,
)
from miniagent.tools.registry import ToolRegistry
from tests.runtime.test_runtime import AllowPolicyEngine, QueueProvider, agent


class LineArguments(ToolArguments):
    line: str
    status: str = "success"


class LineTool(Tool[LineArguments]):
    arguments_model = LineArguments

    def __init__(self, path: Path):
        self.path = path

    @property
    def definition(self):
        return ToolDefinition(
            name="line",
            description="append a line",
            input_schema=LineArguments.model_json_schema(),
        )

    async def execute(self, arguments, *, tool_call_id):
        if arguments.line:
            with self.path.open("a") as stream:
                stream.write(arguments.line + "\n")
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus(arguments.status),
            content="confirmed outcome",
            error=ToolError(code="CONFIRMED_FAILURE", message="confirmed failure")
            if arguments.status != "success"
            else None,
        )


async def accept(execution, calls):
    await execution.begin_step(provider="test", model="test-model")
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(tool_calls=tuple(calls)),
            finish_reason=FinishReason.TOOL_CALL,
        ),
        None,
        disposition=ToolsDisposition(),
    )


async def invoke(execution, tool, call, index):
    await execution.begin_tool(
        call_index=index, tool_call_id=call.id, tool_name=call.name
    )
    await execution.begin_tool_execution()
    await execution.complete_tool(
        await tool.execute(
            LineArguments.model_validate(call.arguments), tool_call_id=call.id
        )
    )


async def prefix(
    tmp_path, first_status, complete_batch=False, suffix_status=ToolStatus.SUCCESS
):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    spec = agent(allowed_tools=["line"])
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=spec,
        config=RuntimeConfig(max_steps=3),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=state.identity.run_id, store=store, recovery_context=None
            )
        ),
    )
    tool = LineTool(tmp_path / "effects")
    for _ in range(2):
        call = ToolCall(
            id="warmup", name="line", arguments={"line": "", "status": "error"}
        )
        await accept(execution, [call])
        await invoke(execution, tool, call, 0)
        await execution.complete_tool_step()
    calls = [
        ToolCall(
            id=f"c{i}",
            name="line",
            arguments={
                "line": f"c{i}",
                "status": first_status.value if i == 0 else suffix_status.value,
            },
        )
        for i in range(3)
    ]
    await accept(execution, calls)
    for index, call in enumerate(calls if complete_batch else calls[:1]):
        await invoke(execution, tool, call, index)
    reopened = SQLiteRunStore(store.path)
    record = await reopened.get(state.identity.run_id)
    return (
        reopened,
        RunExecution.attach(
            record.state, RunJournal.attach(RunRecorder.attach(reopened, record), [])
        ),
        tool,
    )


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
