from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue, ValidationError

from miniagent.models.tool import (
    ToolArgumentIssue,
    ToolCall,
    ToolDefinition,
    ToolError,
)
from miniagent.tools import Tool, ToolArguments


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    tool: Tool
    arguments: dict[str, Any]


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        self._definitions: dict[str, ToolDefinition] = {}
        self._arguments_models: dict[str, type[ToolArguments]] = {}
        for tool in tools:
            definition = tool.definition
            if definition.name in self._tools:
                raise ValueError(f"Duplicate tool name: {definition.name}")
            arguments_model = getattr(tool, "arguments_model", None)
            if not isinstance(arguments_model, type) or not issubclass(
                arguments_model, ToolArguments
            ):
                raise TypeError(
                    f"Tool {definition.name!r} requires a ToolArguments model"
                )
            expected_schema = arguments_model.model_json_schema()
            if definition.input_schema != expected_schema:
                raise ValueError(
                    f"Tool {definition.name!r} input schema does not match "
                    "its arguments model"
                )
            self._tools[definition.name] = tool
            self._definitions[definition.name] = definition.model_copy(deep=True)
            self._arguments_models[definition.name] = arguments_model

    def __getitem__(self, name: str) -> Tool:
        return self._tools[name]

    def definition(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name].model_copy(deep=True)
        except KeyError:
            raise KeyError(f"Tool is not registered: {name}") from None

    def policy_metadata(self, name: str) -> dict[str, JsonValue]:
        return self[name].policy_metadata.copy()

    def definitions(self, allowed_tools: Sequence[str]) -> list[ToolDefinition]:
        duplicates = _duplicates(allowed_tools)
        if duplicates:
            names = ", ".join(duplicates)
            raise ValueError(f"Agent tool list contains duplicates: {names}")

        unknown = sorted(set(allowed_tools) - self._tools.keys())
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"Agent references unknown tools: {names}")

        return [
            self._definitions[name].model_copy(deep=True) for name in allowed_tools
        ]

    def prepare(self, call: ToolCall) -> PreparedToolCall | ToolError:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolError(
                code="TOOL_NOT_FOUND",
                message=f"Tool is not registered: {call.name}",
            )

        try:
            arguments = self._arguments_models[call.name].model_validate(
                call.arguments, strict=True
            ).model_dump(mode="python")
        except ValidationError as exc:
            return ToolError(
                code="INVALID_TOOL_ARGUMENTS",
                message="Tool arguments did not pass validation.",
                issues=[
                    ToolArgumentIssue(
                        location=list(issue["loc"]),
                        code=issue["type"],
                        message=issue["msg"],
                    )
                    for issue in exc.errors(include_url=False)[:8]
                ],
            )
        return PreparedToolCall(tool=tool, arguments=arguments)


def _duplicates(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return sorted(duplicates)
