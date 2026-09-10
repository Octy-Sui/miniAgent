# File   : __init__.py
# Created: 2026-09-09 16:59:51
# Author : octy Sui
from abc import ABC, abstractmethod
from typing import Any

import orjson

from miniagent.models.model import ModelRequest, ModelResponse


class ModelProvider(ABC):
    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse: ...

    def _parse_arguments(self, raw: str) -> dict[str, Any]:
        try:
            result = orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise ValueError(f"Invalid tool arguments: {raw}") from exc

        if not isinstance(result, dict):
            raise TypeError("Tool arguments must be a JSON object")

        return result
