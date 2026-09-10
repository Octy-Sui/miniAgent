# File   : model.py
# Created: 2026-09-09 14:49:25
# Author : octy Sui


from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from miniagent.models.tool import ToolCall, ToolDefinition


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None

    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class ModelRequest(BaseModel):
    model: str
    messages: list[Message]

    tools: list[ToolDefinition] = Field(default_factory=list)

    max_output_tokens: int | None = None
    temperature: float | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALL = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class ModelError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ModelResponse(BaseModel):
    message: Message

    finish_reason: FinishReason

    usage: Usage | None = None

    provider_request_id: str | None = None
    latency_ms: int | None = None

    error: ModelError | None = None
