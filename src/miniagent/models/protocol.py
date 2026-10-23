from enum import StrEnum
from typing import Literal, Protocol

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


class ModelRequestTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_duration_ms: float = Field(ge=0, allow_inf_nan=False)
    server_duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provider_request_id: str | None = None


class ModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: AssistantMessage
    finish_reason: FinishReason
    usage: ModelUsage | None = None
    usage_issue: Literal["missing", "invalid"] | None = None
    usage_evidence: dict[str, int | None] | None = None
    provider_request_id: str | None = None
    latency_ms: int | None = None
    request_timing: ModelRequestTiming | None = None
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
    request_timing: ModelRequestTiming | None = None


class ModelProvider(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


class ModelFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: Literal[
        "rate_limit",
        "server",
        "gateway",
        "connection",
        "read_timeout",
        "permanent",
        "unknown",
    ] = "unknown"
    retryable: bool = False
    status_code: int | None = None
    provider_code: str | None = None
    exception_type: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        request_timing: ModelRequestTiming | None = None,
        failure: ModelFailure | None = None,
    ) -> None:
        self.failure = failure or ModelFailure(retryable=retryable)
        self.retryable = self.failure.retryable
        self.request_timing = request_timing
        super().__init__(message)


class ModelContextLimitError(ModelProviderError):
    pass


class ModelResponseNormalizationError(ModelProviderError):
    def __init__(self, *, evidence: ProviderResponseEvidence, code: str) -> None:
        self.evidence = evidence
        self.code = code
        super().__init__(
            "Model response normalization failed",
            request_timing=evidence.request_timing,
        )
