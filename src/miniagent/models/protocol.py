from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.models.config import GenerationOptions
from miniagent.models.messages import AssistantMessage, Message
from miniagent.tools.contracts import ToolDefinition


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDefinition, ...] = ()
    generation: GenerationOptions = GenerationOptions()


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_token_breakdown(self) -> "ModelUsage":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed input tokens")
        if self.reasoning_output_tokens > self.output_tokens:
            raise ValueError("Reasoning output tokens cannot exceed output tokens")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("Total tokens must equal input plus output tokens")
        return self

    @property
    def uncached_input_tokens(self) -> int:
        return self.input_tokens - self.cached_input_tokens


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALL = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class ModelError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: AssistantMessage
    finish_reason: FinishReason
    usage: ModelUsage | None = None
    provider_request_id: str | None = None
    latency_ms: int | None = None
    error: ModelError | None = None


class ProviderToolCallEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = None
    name: str | None = None
    arguments: str | None = None


class ProviderResponseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str | None = None
    tool_calls: tuple[ProviderToolCallEvidence, ...] = ()
    finish_reason: str | None = None
    provider_request_id: str | None = None
    usage: dict[str, int | None] | None = None


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class ModelProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


class ModelContextLimitError(ModelProviderError):
    pass


class ModelResponseNormalizationError(ModelProviderError):
    def __init__(self, *, evidence: ProviderResponseEvidence, code: str) -> None:
        self.evidence = evidence
        self.code = code
        super().__init__("Model response normalization failed")
