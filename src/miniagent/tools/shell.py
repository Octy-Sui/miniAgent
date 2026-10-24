# File   : shell.py
# Created: 2026-09-09 17:41:33
# Author : octy Sui

from pydantic import Field, JsonValue

from miniagent.environments import Command, ExecutionStatus, Workspace
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import (
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolStatus,
)
from miniagent.tools.execution_result import tool_result_from_execution


class ShellArguments(ToolArguments):
    command: str = Field(min_length=1)


class ShellTool(Tool[ShellArguments]):
    arguments_model = ShellArguments

    def __init__(
        self,
        workspace: Workspace,
        timeout: float = 30.0,
    ) -> None:
        self.workspace = workspace
        self.timeout = timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell",
            description=("Execute a shell command inside the workspace."),
            input_schema=self.arguments_model.model_json_schema(),
        )

    @property
    def execution_options(self) -> dict[str, JsonValue]:
        return {"timeout_seconds": self.timeout}

    async def execute(
        self,
        arguments: ShellArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        command = arguments.command

        result = await self.workspace.execute(
            Command(
                argv=["/bin/sh", "-lc", command],
                timeout_seconds=self.timeout,
            )
        )
        if result.status is ExecutionStatus.TIMEOUT:
            return tool_result_from_execution(
                result,
                tool_call_id=tool_call_id,
                status=ToolStatus.TIMEOUT,
                data={
                    "command": command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.exit_code,
                },
                error=ToolError(
                    code="COMMAND_TIMEOUT",
                    message="Shell command timed out.",
                ),
            )
        exit_code = result.exit_code

        return tool_result_from_execution(
            result,
            tool_call_id=tool_call_id,
            status=(ToolStatus.SUCCESS if exit_code == 0 else ToolStatus.ERROR),
            data={
                "command": command,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": exit_code,
            },
            metrics={
                "exit_code": exit_code,
            },
            error=(
                None
                if result.status is ExecutionStatus.SUCCESS
                else ToolError(
                    code="COMMAND_FAILED",
                    message=(f"Command exited with code {exit_code}"),
                )
            ),
        )
