import asyncio

from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import ToolDefinition, ToolResult, ToolStatus


class EmptyArguments(ToolArguments):
    pass


class BlockingTool(Tool[EmptyArguments]):
    arguments_model = EmptyArguments

    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = False

    @property
    def definition(self):
        return ToolDefinition(
            name="block",
            description="controlled boundary",
            input_schema=EmptyArguments.model_json_schema(),
        )

    async def execute(self, arguments, *, tool_call_id):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.SUCCESS,
                content="must not be invented as completed",
            )
