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
    data: dict[str, JsonValue] | None = None,
    error: ToolError | None = None,
    metrics: dict[str, JsonValue] | None = None,
) -> ToolResult:
    artifacts = list(execution.artifacts)
    if execution.output_evidence is not None:
        artifacts.extend(
            stream.ref
            for stream in (
                execution.output_evidence.stdout,
                execution.output_evidence.stderr,
            )
            if stream.ref is not None
        )
    return ToolResult(
        tool_call_id=tool_call_id,
        status=status,
        data=data or {},
        duration_ms=execution.duration_ms,
        error=error,
        artifacts=list(dict.fromkeys(artifacts)),
        metrics=metrics or {},
        workspace_delta=execution.workspace_delta,
        output=ToolOutputInfo(
            stdout_truncated=execution.stdout_truncated,
            stderr_truncated=execution.stderr_truncated,
        ),
    )
