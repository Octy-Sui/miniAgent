from uuid import UUID, uuid4

from pydantic import Field, JsonValue

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments.contracts import (
    Command,
    ExecutionResult,
    ExecutionStatus,
    Patch,
    WorkspaceCheckpoint,
    WorkspaceDelta,
)
from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import (
    ToolDefinition,
    ToolResult,
    ToolStatus,
)


class TextArguments(ToolArguments):
    value: str = Field(min_length=1)


class TextTool(Tool[TextArguments]):
    arguments_model: type[TextArguments] = TextArguments
    contract_version = "text-v1"

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="text",
            description="Return text.",
            input_schema=self.arguments_model.model_json_schema(),
        )

    @property
    def execution_options(self) -> dict[str, JsonValue]:
        return {"timeout_seconds": 2}

    async def execute(
        self,
        arguments: TextArguments,
        *,
        tool_call_id: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content=arguments.value,
        )


class EvidenceWorkspace:
    def describe_attachment(self):
        raise AssertionError("Attachment metadata is not used by this test")

    run_id = UUID("00000000-0000-0000-0000-000000000001")
    workspace_id = "workspace"

    def __init__(self, result: ExecutionResult) -> None:
        self.result = result

    async def execute(self, command: Command) -> ExecutionResult:
        return self.result

    async def checkpoint(self) -> WorkspaceCheckpoint:
        raise AssertionError("not used")

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None:
        raise AssertionError("not used")

    async def export_patch(
        self,
        *,
        timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
        temporary_index_path: str | None = None,
    ) -> Patch:
        raise AssertionError("not used")

    async def destroy(self) -> None:
        raise AssertionError("not used")


def execution_with_evidence(
    status: ExecutionStatus,
    exit_code: int,
) -> tuple[ExecutionResult, ArtifactRef, WorkspaceDelta]:
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        uri="file:///artifact",
        type="command_output",
    )
    delta = WorkspaceDelta(files_modified=["changed.txt"])
    execution = ExecutionResult(
        status=status,
        exit_code=exit_code,
        stdout="output",
        stderr="error",
        stdout_truncated=True,
        duration_ms=17,
        artifacts=[artifact],
        workspace_delta=delta,
    )
    return execution, artifact, delta
