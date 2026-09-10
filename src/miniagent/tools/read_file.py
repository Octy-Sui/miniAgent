# File   : read_file.py
# Created: 2026-09-09 17:39:55
# Author : octy Sui

from pathlib import Path
from typing import Any

from miniagent.models.tool import (
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolStatus,
)
from miniagent.tools import Tool


class ReadFileTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

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

        target = (self.workspace / path).resolve()

        if not target.is_relative_to(self.workspace):
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.DENIED,
                content=f"Access denied: {path}",
                error=ToolError(
                    code="PATH_OUTSIDE_WORKSPACE",
                    message="Path escapes workspace.",
                ),
            )

        if not target.is_file():
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"File not found: {path}",
                error=ToolError(
                    code="FILE_NOT_FOUND",
                    message=f"File does not exist: {path}",
                ),
            )

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Cannot read binary file: {path}",
                error=ToolError(
                    code="DECODE_ERROR",
                    message="File is not valid UTF-8 text.",
                ),
            )

        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content=content,
            data={
                "path": path,
                "size": target.stat().st_size,
            },
        )
