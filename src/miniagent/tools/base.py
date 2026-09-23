from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, JsonValue

from miniagent.tools.contracts import ToolDefinition, ToolResult


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Tool[ArgumentsT: ToolArguments](ABC):
    arguments_model: type[ArgumentsT]
    contract_version = "1"

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @property
    def execution_options(self) -> dict[str, JsonValue]:
        return {}

    @abstractmethod
    async def execute(
        self,
        arguments: ArgumentsT,
        *,
        tool_call_id: str,
    ) -> ToolResult: ...
