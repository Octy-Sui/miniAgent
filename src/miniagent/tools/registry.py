from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import orjson
from pydantic import JsonValue, ValidationError

from miniagent.tools.base import Tool, ToolArguments
from miniagent.tools.contracts import (
    ToolArgumentIssue,
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolResult,
)


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    canonical_arguments: dict[str, JsonValue]
    _invoke: Callable[[str], Awaitable[ToolResult]]

    async def invoke(self, tool_call_id: str) -> ToolResult:
        return await self._invoke(tool_call_id)


@dataclass(frozen=True, slots=True)
class _ToolBinding[ArgumentsT: ToolArguments]:
    tool: Tool[ArgumentsT]
    arguments_model: type[ArgumentsT]
    definition: ToolDefinition
    contract_version: str
    execution_options: dict[str, JsonValue]

    def prepare(self, call: ToolCall) -> PreparedToolCall | ToolError:
        try:
            arguments = self.arguments_model.model_validate(
                call.arguments,
                strict=True,
            )
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
        canonical_arguments: dict[str, JsonValue] = orjson.loads(
            orjson.dumps(arguments.model_dump(mode="json"))
        )

        async def invoke(tool_call_id: str) -> ToolResult:
            return await self.tool.execute(arguments, tool_call_id=tool_call_id)

        return PreparedToolCall(
            canonical_arguments=canonical_arguments,
            _invoke=invoke,
        )


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool[Any]]) -> None:
        self._bindings: dict[str, _ToolBinding[Any]] = {}
        for tool in tools:
            definition = tool.definition.model_copy(deep=True)
            if definition.name in self._bindings:
                raise ValueError(f"Duplicate tool name: {definition.name}")
            arguments_model = getattr(tool, "arguments_model", None)
            if not isinstance(arguments_model, type) or not issubclass(
                arguments_model,
                ToolArguments,
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
            execution_options: dict[str, JsonValue] = orjson.loads(
                orjson.dumps(tool.execution_options)
            )
            self._bindings[definition.name] = _ToolBinding(
                tool=tool,
                arguments_model=arguments_model,
                definition=definition,
                contract_version=tool.contract_version,
                execution_options=execution_options,
            )

    def __getitem__(self, name: str) -> Tool[Any]:
        return self._binding(name).tool

    def definition(self, name: str) -> ToolDefinition:
        return self._binding(name).definition.model_copy(deep=True)

    def contract_metadata(self, name: str) -> dict[str, JsonValue]:
        binding = self._binding(name)
        return {
            "name": binding.definition.name,
            "version": binding.contract_version,
            "input_schema": orjson.loads(orjson.dumps(binding.definition.input_schema)),
            "execution_options": orjson.loads(
                orjson.dumps(binding.execution_options)
            ),
        }

    def definitions(self, allowed_tools: Sequence[str]) -> list[ToolDefinition]:
        duplicates = _duplicates(allowed_tools)
        if duplicates:
            names = ", ".join(duplicates)
            raise ValueError(f"Agent tool list contains duplicates: {names}")
        unknown = sorted(set(allowed_tools) - self._bindings.keys())
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"Agent references unknown tools: {names}")
        return [self.definition(name) for name in allowed_tools]

    def prepare(self, call: ToolCall) -> PreparedToolCall | ToolError:
        binding = self._bindings.get(call.name)
        if binding is None:
            return ToolError(
                code="TOOL_NOT_FOUND",
                message=f"Tool is not registered: {call.name}",
            )
        return binding.prepare(call)

    def _binding(self, name: str) -> _ToolBinding[Any]:
        try:
            return self._bindings[name]
        except KeyError:
            raise KeyError(f"Tool is not registered: {name}") from None


def _duplicates(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return sorted(duplicates)
