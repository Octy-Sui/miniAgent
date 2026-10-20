from pathlib import Path
from uuid import uuid4

from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import (
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
from tests.support.runtime.runtime import agent


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
            record, RunJournal.attach(RunRecorder.attach(reopened, record), [])
        ),
        tool,
    )
