# File   : shell.py
# Created: 2026-09-09 17:41:33
# Author : octy Sui

from typing import Any

from pydantic import Field, JsonValue

from miniagent.environments import Command, ExecutionStatus, Workspace
from miniagent.models.tool import (
    ToolDefinition,
    ToolError,
    ToolOutputInfo,
    ToolResult,
    ToolStatus,
)
from miniagent.tools import Tool, ToolArguments


class ShellArguments(ToolArguments):
    command: str = Field(min_length=1)


class ShellTool(Tool):
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
    def policy_metadata(self) -> dict[str, JsonValue]:
        return super().policy_metadata | {"timeout_seconds": self.timeout}

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        command = arguments["command"]

        result = await self.workspace.execute(
            Command(
                argv=["/bin/sh", "-lc", command],
                timeout_seconds=self.timeout,
            )
        )
        if result.status is ExecutionStatus.TIMEOUT:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.TIMEOUT,
                content=(f"Command timed out after {self.timeout}s:\n{command}"),
                error=ToolError(
                    code="COMMAND_TIMEOUT",
                    message="Shell command timed out.",
                ),
                output=ToolOutputInfo(
                    stdout_truncated=result.stdout_truncated,
                    stderr_truncated=result.stderr_truncated,
                ),
            )
        exit_code = result.exit_code

        content = (
            f"$ {command}\n"
            f"\nstdout:\n{result.stdout}"
            f"\nstderr:\n{result.stderr}"
            f"\nexit_code: {exit_code}"
        )

        return ToolResult(
            tool_call_id=tool_call_id,
            status=(ToolStatus.SUCCESS if exit_code == 0 else ToolStatus.ERROR),
            content=content,
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
            output=ToolOutputInfo(
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
            ),
        )
