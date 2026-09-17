# File   : __init__.py
# Created: 2026-09-09 17:39:20
# Author : octy Sui

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from miniagent.models.tool import ToolDefinition, ToolResult


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Tool(ABC):
    arguments_model: type[ToolArguments]

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @property
    def policy_metadata(self) -> dict[str, JsonValue]:
        tool_type = type(self)
        return {"implementation": f"{tool_type.__module__}.{tool_type.__qualname__}"}

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult: ...
