# File   : shell.py
# Created: 2026-09-09 17:41:33
# Author : octy Sui

import asyncio
from pathlib import Path
from typing import Any

from miniagent.models.tool import ToolDefinition, ToolError, ToolResult, ToolStatus
from miniagent.tools import Tool


class ShellTool(Tool):
    def __init__(
        self,
        workspace: Path,
        timeout: float = 30.0,
    ) -> None:
        self.workspace = workspace.resolve()
        self.timeout = timeout

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="shell",
            description=("Execute a shell command inside the workspace."),
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        command = arguments["command"]

        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()

            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.TIMEOUT,
                content=(f"Command timed out after {self.timeout}s:\n{command}"),
                error=ToolError(
                    code="COMMAND_TIMEOUT",
                    message="Shell command timed out.",
                    retryable=True,
                ),
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        exit_code = process.returncode

        content = (
            f"$ {command}\n"
            f"\nstdout:\n{stdout_text}"
            f"\nstderr:\n{stderr_text}"
            f"\nexit_code: {exit_code}"
        )

        return ToolResult(
            tool_call_id=tool_call_id,
            status=(ToolStatus.SUCCESS if exit_code == 0 else ToolStatus.ERROR),
            content=content,
            data={
                "command": command,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "exit_code": exit_code,
            },
            metrics={
                "exit_code": exit_code,
            },
            error=(
                None
                if exit_code == 0
                else ToolError(
                    code="COMMAND_FAILED",
                    message=(f"Command exited with code {exit_code}"),
                )
            ),
        )
