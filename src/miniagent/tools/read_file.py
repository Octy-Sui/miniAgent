# File   : read_file.py
# Created: 2026-09-09 17:39:55
# Author : octy Sui

from pathlib import PurePosixPath

from pydantic import Field

from miniagent.environments import Command, ExecutionStatus, Workspace
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import (
    ToolDefinition,
    ToolError,
    ToolResult,
    ToolStatus,
)
from miniagent.tools.execution_result import tool_result_from_execution


class ReadFileArguments(ToolArguments):
    path: str = Field(
        min_length=1,
        description="Path relative to the workspace root.",
    )


class ReadFileTool(Tool[ReadFileArguments]):
    arguments_model = ReadFileArguments

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read a UTF-8 text file from the workspace.",
            input_schema=self.arguments_model.model_json_schema(),
        )

    async def execute(
        self,
        arguments: ReadFileArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        path = arguments.path

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
                        "root=pathlib.Path.cwd().resolve(); "
                        "path=pathlib.Path(sys.argv[1]).resolve(); "
                        "sys.exit(3) if not path.is_relative_to(root) else None; "
                        "sys.exit(2) if not path.is_file() else None; "
                        "content=path.read_bytes(); "
                        "sys.stdout.write(content.decode('utf-8'))"
                    ),
                    str(relative),
                ]
            )
        )
        if result.exit_code == 3:
            return tool_result_from_execution(
                result,
                tool_call_id=tool_call_id,
                status=ToolStatus.DENIED,
                content=f"Access denied: {path}",
                error=ToolError(
                    code="PATH_OUTSIDE_WORKSPACE",
                    message="Resolved path escapes workspace.",
                ),
            )
        if result.exit_code == 2:
            return tool_result_from_execution(
                result,
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"File not found: {path}",
                error=ToolError(
                    code="FILE_NOT_FOUND",
                    message=f"File does not exist: {path}",
                ),
            )
        if "UnicodeDecodeError" in result.stderr:
            return tool_result_from_execution(
                result,
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Cannot read binary file: {path}",
                error=ToolError(
                    code="DECODE_ERROR",
                    message="File is not valid UTF-8 text.",
                ),
            )
        if result.status is not ExecutionStatus.SUCCESS:
            return tool_result_from_execution(
                result,
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Cannot read file: {path}\n{result.stderr}",
                error=ToolError(
                    code="READ_FAILED",
                    message=f"Cannot read file: {path}",
                ),
            )
        if result.stdout_truncated:
            return tool_result_from_execution(
                result,
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

        return tool_result_from_execution(
            result,
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content=result.stdout,
            data={
                "path": path,
                "size": len(result.stdout.encode()),
            },
        )
