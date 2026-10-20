from uuid import uuid4

from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    current_execution_observer,
)
from miniagent.tools.base import Tool
from miniagent.tools.contracts import ToolDefinition, ToolResult, ToolStatus
from tests.support.runtime.runtime import (
    EmptyArguments,
)


class ObservedTool(Tool[EmptyArguments]):
    arguments_model = EmptyArguments

    def __init__(self):
        self.effects = []

    @property
    def definition(self):
        return ToolDefinition(
            name="observed",
            description="Observed boundary",
            input_schema=EmptyArguments.model_json_schema(),
        )

    async def execute(self, arguments, *, tool_call_id):
        observer = current_execution_observer()
        assert observer is not None
        identity = HostProcessIdentity(
            role="local_command",
            attempt_id=observer.attempt_id,
            execution_id=uuid4(),
            pid=123,
            create_time=1,
            host_boot_id="boot",
            pgid=123,
            sid=123,
        )
        await observer.identified(identity)
        self.effects.append("external action permitted")
        return ToolResult(
            tool_call_id=tool_call_id, status=ToolStatus.SUCCESS, content="done"
        )
