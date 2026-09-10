# File   : __init__.py
# Created: 2026-09-09 17:39:20
# Author : octy Sui

from abc import ABC, abstractmethod
from typing import Any

from miniagent.models.tool import ToolDefinition, ToolResult


class Tool(ABC):
    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult: ...
