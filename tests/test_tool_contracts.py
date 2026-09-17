from typing import Any

import orjson
import pytest
from pydantic import Field, ValidationError

from miniagent.models.tool import (
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolOutputLimits,
    ToolResult,
    ToolStatus,
)
from miniagent.runtime.tool_output import render_tool_result
from miniagent.tools import Tool, ToolArguments
from miniagent.tools.registry import PreparedToolCall, ToolRegistry


class TextArguments(ToolArguments):
    value: str = Field(min_length=1)


class TextTool(Tool):
    arguments_model: type[ToolArguments] = TextArguments

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="text",
            description="Return text.",
            input_schema=self.arguments_model.model_json_schema(),
        )

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content=arguments["value"],
        )


def test_registry_validates_arguments_and_isolates_definition_copies() -> None:
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
    assert prepared.arguments == {"value": "hello"}
    assert isinstance(invalid, ToolError)
    assert invalid.code == "INVALID_TOOL_ARGUMENTS"
    assert invalid.issues[0].location == ["value"]


def test_registry_keeps_the_registered_arguments_model() -> None:
    tool = TextTool()
    registry = ToolRegistry([tool])
    tool.arguments_model = ToolArguments

    invalid = registry.prepare(
        ToolCall(id="invalid", name="text", arguments={"unexpected": True})
    )

    assert isinstance(invalid, ToolError)
    assert invalid.code == "INVALID_TOOL_ARGUMENTS"


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
            content="failed",
        )

    with pytest.raises(ValidationError, match="cannot contain an error"):
        ToolResult(
            tool_call_id="call",
            status=ToolStatus.SUCCESS,
            content="done",
            error=ToolError(code="UNEXPECTED", message="Unexpected."),
        )


def test_tool_result_rendering_is_bounded_and_preserves_structure() -> None:
    rendered = render_tool_result(
        ToolResult(
            tool_call_id="call",
            status=ToolStatus.ERROR,
            content="😀" * 100,
            data={"large": "x" * 4_000},
            error=ToolError(
                code="FAILED",
                message="Command failed.",
            ),
        ),
        ToolOutputLimits(max_content_bytes=65, max_message_bytes=2048),
    )
    payload = orjson.loads(rendered)

    assert len(rendered.encode("utf-8")) <= 2048
    assert payload["content"] == "😀" * 16
    assert payload["error"]["code"] == "FAILED"
    assert payload["output"]["content_truncated"] is True
    assert payload["output"]["omitted_fields"] == ["data"]
    assert "data" not in payload


def test_tool_call_id_and_output_limits_are_bounded() -> None:
    with pytest.raises(ValidationError, match="1 to 256"):
        ToolCall(id="😀" * 65, name="text", arguments={})

    with pytest.raises(ValidationError, match="cannot exceed"):
        ToolOutputLimits(max_content_bytes=4096, max_message_bytes=2048)
