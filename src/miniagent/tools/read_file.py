# File   : read_file.py
# Created: 2026-09-09 17:39:55
# Author : octy Sui

from pathlib import PurePosixPath
from typing import Any

from miniagent.environments import Command, ExecutionStatus, Workspace
from miniagent.models.tool import (
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolStatus,
)
from miniagent.tools import Tool


class ReadFileTool(Tool):
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file from the workspace.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ("Path relative to the workspace root."),
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        path = arguments["path"]

        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.DENIED,
                content=f"Access denied: {path}",
                error=ToolError(
                    code="PATH_OUTSIDE_WORKSPACE",
                    message="Path escapes workspace.",
                ),
            )

        result = await self.workspace.execute(
            Command(
                argv=[
                    "python",
                    "-c",
                    (
                        "import pathlib,sys; "
                        "path=pathlib.Path(sys.argv[1]); "
                        "sys.exit(2) if not path.is_file() else None; "
                        "content=path.read_bytes(); "
                        "sys.stdout.write(content.decode('utf-8'))"
                    ),
                    str(relative),
                ]
            )
        )
        if result.exit_code == 2:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"File not found: {path}",
                error=ToolError(
                    code="FILE_NOT_FOUND",
                    message=f"File does not exist: {path}",
                ),
            )
        if "UnicodeDecodeError" in result.stderr:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Cannot read binary file: {path}",
                error=ToolError(
                    code="DECODE_ERROR",
                    message="File is not valid UTF-8 text.",
                ),
            )
        if result.status is not ExecutionStatus.SUCCESS:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Cannot read file: {path}\n{result.stderr}",
                error=ToolError(
                    code="READ_FAILED",
                    message=f"Cannot read file: {path}",
                ),
            )
        if result.stdout_truncated:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=result.stdout,
                data={
                    "path": path,
                    "truncated": True,
                },
                error=ToolError(
                    code="OUTPUT_TRUNCATED",
                    message="File exceeds the workspace output limit.",
                ),
            )

        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content=result.stdout,
            data={
                "path": path,
                "size": len(result.stdout.encode()),
            },
        )
