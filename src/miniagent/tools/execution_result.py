from pydantic import JsonValue

from miniagent.environments.contracts import ExecutionResult
from miniagent.tools.contracts import (
    ToolError,
    ToolOutputInfo,
    ToolResult,
    ToolStatus,
)


def tool_result_from_execution(
    execution: ExecutionResult,
    *,
    tool_call_id: str,
    status: ToolStatus,
    content: str,
    data: dict[str, JsonValue] | None = None,
    error: ToolError | None = None,
    metrics: dict[str, JsonValue] | None = None,
) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        status=status,
        content=content,
        duration_ms=execution.duration_ms,
        data=data,
        error=error,
        artifacts=list(execution.artifacts),
        metrics=metrics or {},
        workspace_delta=execution.workspace_delta,
        output=ToolOutputInfo(
            stdout_truncated=execution.stdout_truncated,
            stderr_truncated=execution.stderr_truncated,
        ),
    )
