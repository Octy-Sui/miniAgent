from typing import cast

import orjson
import pytest
from pydantic import ValidationError

from miniagent.environments.contracts import (
    ExecutionResult,
    ExecutionStatus,
)
from miniagent.tools.base import ToolArguments
from miniagent.tools.contracts import (
    ToolCall,
    ToolError,
    ToolOutputLimits,
    ToolResult,
    ToolStatus,
)
from miniagent.tools.read_file import ReadFileArguments, ReadFileTool
from miniagent.tools.registry import PreparedToolCall, ToolRegistry
from miniagent.tools.rendering import render_tool_result
from miniagent.tools.shell import ShellArguments, ShellTool
from tests.support.tools.contracts import (
    EvidenceWorkspace,
    TextArguments,
    TextTool,
    execution_with_evidence,
)


async def test_registry_validates_arguments_and_isolates_definition_copies() -> None:
    registry = ToolRegistry([TextTool()])
    definitions = registry.definitions(["text"])
    definitions[0].input_schema.clear()

    prepared = registry.prepare(
        ToolCall(id="valid", name="text", arguments={"value": "hello"})
    )
    invalid = registry.prepare(
        ToolCall(id="invalid", name="text", arguments={"value": 1})
    )

    assert registry.definitions(["text"])[0].input_schema
    assert isinstance(prepared, PreparedToolCall)
    assert prepared.canonical_arguments == {"value": "hello"}
    assert (await prepared.invoke("valid")).data["text"] == "hello"
    assert isinstance(invalid, ToolError)
    assert invalid.code == "INVALID_TOOL_ARGUMENTS"
    assert invalid.issues[0].location == ["value"]


def test_registry_keeps_the_registered_arguments_model() -> None:
    tool = TextTool()
    registry = ToolRegistry([tool])
    tool.arguments_model = cast(type[TextArguments], ToolArguments)

    invalid = registry.prepare(
        ToolCall(id="invalid", name="text", arguments={"unexpected": True})
    )

    assert isinstance(invalid, ToolError)
    assert invalid.code == "INVALID_TOOL_ARGUMENTS"


def test_tool_contract_metadata_is_logical_and_snapshotted() -> None:
    tool = TextTool()
    registry = ToolRegistry([tool])

    metadata = registry.contract_metadata("text")
    assert metadata["docker_helper_protocol"] == 1
    assert metadata["docker_supervisor_protocol"] == 1
    tool.contract_version = "changed"

    assert metadata == {
        "name": "text",
        "version": "text-v1",
        "unknown_outcome": "reject",
        "docker_helper_protocol": 1,
        "docker_supervisor_protocol": 1,
        "input_schema": TextArguments.model_json_schema(),
        "execution_options": {"timeout_seconds": 2},
    }
    assert "implementation" not in metadata
    assert registry.contract_metadata("text")["version"] == "text-v1"


def test_registry_rejects_duplicate_tools_and_agent_names() -> None:
    with pytest.raises(ValueError, match="Duplicate tool name"):
        ToolRegistry([TextTool(), TextTool()])

    with pytest.raises(ValueError, match="contains duplicates"):
        ToolRegistry([TextTool()]).definitions(["text", "text"])


def test_tool_result_requires_error_to_match_status() -> None:
    with pytest.raises(ValidationError, match="requires an error"):
        ToolResult(
            tool_call_id="call",
            status=ToolStatus.ERROR,
            data={"text": "failed"},
        )

    with pytest.raises(ValidationError, match="cannot contain an error"):
        ToolResult(
            tool_call_id="call",
            status=ToolStatus.SUCCESS,
            data={"text": "done"},
            error=ToolError(code="UNEXPECTED", message="Unexpected."),
        )


def test_tool_result_rendering_is_bounded_and_preserves_structure() -> None:
    result = ToolResult(
        tool_call_id="call",
        status=ToolStatus.ERROR,
        data={"text": "😀" * 100, "large": "x" * 4_000},
        duration_ms=12,
        error=ToolError(
            code="FAILED",
            message="Command failed.",
        ),
    )
    original = result.model_dump()

    rendered = render_tool_result(
        result,
        ToolOutputLimits(max_content_bytes=65, max_message_bytes=2048),
    )
    payload = orjson.loads(rendered)

    assert len(rendered.encode("utf-8")) <= 2048
    assert payload["data"]["text"] == "😀" * 16
    assert payload["error"]["code"] == "FAILED"
    assert payload["output"]["content_truncated"] is True
    assert payload["output"]["omitted_fields"] == []
    assert payload["data"]["large"] == "x" * 65
    assert payload["duration_ms"] == 12
    assert result.model_dump() == original


def test_tool_call_id_and_output_limits_are_bounded() -> None:
    with pytest.raises(ValidationError, match="1 to 256"):
        ToolCall(id="😀" * 65, name="text", arguments={})

    with pytest.raises(ValidationError, match="cannot exceed"):
        ToolOutputLimits(max_content_bytes=4096, max_message_bytes=2048)


async def test_shell_preserves_successful_workspace_execution_evidence() -> None:
    execution, artifact, delta = execution_with_evidence(
        ExecutionStatus.SUCCESS,
        0,
    )

    result = await ShellTool(EvidenceWorkspace(execution)).execute(
        ShellArguments(command="true"),
        tool_call_id="call",
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.duration_ms == 17
    assert result.artifacts == [artifact]
    assert result.workspace_delta == delta
    assert result.output.stdout_truncated


async def test_read_file_preserves_failed_workspace_execution_evidence() -> None:
    execution, artifact, delta = execution_with_evidence(
        ExecutionStatus.ERROR,
        2,
    )

    result = await ReadFileTool(EvidenceWorkspace(execution)).execute(
        ReadFileArguments(path="missing.txt"),
        tool_call_id="call",
    )

    assert result.status is ToolStatus.ERROR
    assert result.duration_ms == 17
    assert result.artifacts == [artifact]
    assert result.workspace_delta == delta
    assert result.output.stdout_truncated


def test_unknown_outcome_is_part_of_tool_fingerprint() -> None:
    """TC-U6: replay policy is explicit, snapshotted, and defaults to rejection."""
    workspace = EvidenceWorkspace(
        ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=0,
        )
    )
    registry = ToolRegistry([ReadFileTool(workspace), ShellTool(workspace), TextTool()])
    assert (
        registry.contract_metadata("read_file")["unknown_outcome"] == "retry_read_only"
    )
    assert registry.contract_metadata("shell")["unknown_outcome"] == "reject"
    assert registry.contract_metadata("text")["unknown_outcome"] == "reject"
