# File   : qwen.py
# Created: 2026-09-09 17:02:08
# Author : octy Sui

from time import perf_counter
from typing import Any

import orjson
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, omit
from pydantic import BaseModel, Field, ValidationError, field_validator

from miniagent.models.messages import AssistantMessage, Message
from miniagent.models.protocol import (
    FinishReason,
    ModelContextLimitError,
    ModelProviderError,
    ModelRequest,
    ModelRequestTiming,
    ModelResponse,
    ModelResponseNormalizationError,
    ModelUsage,
    ProviderResponseEvidence,
    ProviderToolCallEvidence,
)
from miniagent.tools.contracts import ToolCall


class _ServerMetadata(BaseModel):
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    request_id: str | None = None

    @field_validator("duration", mode="before")
    @classmethod
    def reject_boolean_duration(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Duration must be milliseconds")  # noqa: TRY004 -- Pydantic validation must remain recoverable.
        return value


def _request_timing(
    started: float, metadata: object, request_id: str | None = None
) -> ModelRequestTiming:
    elapsed = (perf_counter() - started) * 1000
    try:
        server = _ServerMetadata.model_validate(
            metadata if isinstance(metadata, dict) else {}
        )
    except ValidationError:
        server = _ServerMetadata()
    return ModelRequestTiming(
        client_duration_ms=elapsed,
        server_duration_ms=server.duration,
        provider_request_id=server.request_id or request_id,
    )


class QwenProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
        )

    def _to_provider_message(self, message: Message) -> dict[str, Any]:
        """
        Convert internal Message to OpenAI-compatible/Qwen message format.
        """
        result: dict[str, Any] = {
            "role": message.role,
        }

        if message.content is not None:
            result["content"] = message.content

        # assistant message with tool calls
        if message.role == "assistant" and message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": orjson.dumps(call.arguments).decode("utf-8"),
                    },
                }
                for call in message.tool_calls
            ]

            # OpenAI-compatible APIs usually accept content=None
            # when assistant only emits tool calls.
            result.setdefault("content", None)

        # tool result message
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("tool message requires tool_call_id")

            result["tool_call_id"] = message.tool_call_id

        return result

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        try:
            result = orjson.loads(raw)
        except orjson.JSONDecodeError as exc:
            raise ValueError(f"Invalid tool arguments: {raw}") from exc
        if not isinstance(result, dict):
            raise TypeError("Tool arguments must be a JSON object")
        return result

    @staticmethod
    def _map_finish_reason(finish_reason: str | None) -> FinishReason:
        """
        Normalize provider-specific finish reason to internal enum.
        """
        if finish_reason is None:
            return FinishReason.UNKNOWN

        mapping = {
            "stop": FinishReason.STOP,
            # OpenAI / Qwen OpenAI-compatible API
            "tool_calls": FinishReason.TOOL_CALL,
            "length": FinishReason.LENGTH,
            "content_filter": FinishReason.CONTENT_FILTER,
            "error": FinishReason.ERROR,
        }

        return mapping.get(finish_reason, FinishReason.UNKNOWN)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        started = perf_counter()
        try:
            response = await self.client.chat.completions.create(
                model=request.model,
                messages=[
                    self._to_provider_message(message) for message in request.messages
                ],  # ty: ignore[invalid-argument-type]
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        },
                    }
                    for tool in request.tools
                ]
                if request.tools
                else omit,
                temperature=request.generation.temperature,
                max_tokens=request.generation.max_output_tokens,
            )
        except APIStatusError as exc:
            try:
                metadata = exc.response.json()
            except ValueError:
                metadata = exc.body
            timing = _request_timing(started, metadata, exc.request_id)
            code = str(exc.code or "").lower()
            message = exc.message.lower()
            safe_message = f"Provider HTTP request failed (status={exc.status_code})"
            if exc.status_code in (400, 413) and (
                code
                in {
                    "context_length_exceeded",
                    "context_window_exceeded",
                    "inputtokensexceedlimit",
                }
                or "maximum context length" in message
            ):
                raise ModelContextLimitError(
                    safe_message, request_timing=timing
                ) from None
            if exc.status_code == 429 or exc.status_code >= 500:
                raise ModelProviderError(
                    safe_message, retryable=True, request_timing=timing
                ) from None
            error = type(exc)(safe_message, response=exc.response, body=None)
            error.__dict__["request_timing"] = timing
            raise error from None
        except APIConnectionError:
            raise ModelProviderError(
                "Provider connection failed",
                retryable=True,
                request_timing=_request_timing(started, {}),
            ) from None

        timing = _request_timing(started, response.model_extra, response.id)

        def text(value: object) -> str | None:
            return value if isinstance(value, str) else None

        choice = (
            response.choices[0]
            if isinstance(response.choices, list) and response.choices
            else None
        )
        provider_message = getattr(choice, "message", None)
        usage = response.usage
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        raw_usage = (
            {
                "input_tokens": getattr(usage, "prompt_tokens", None),
                "cached_input_tokens": getattr(prompt_details, "cached_tokens", None),
                "output_tokens": getattr(usage, "completion_tokens", None),
                "reasoning_output_tokens": getattr(
                    completion_details, "reasoning_tokens", None
                ),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
            if usage is not None
            else None
        )
        calls = getattr(provider_message, "tool_calls", None)
        evidence = ProviderResponseEvidence(
            request_timing=timing,
            content=text(getattr(provider_message, "content", None)),
            tool_calls=tuple(
                ProviderToolCallEvidence(
                    id=text(getattr(call, "id", None)),
                    name=text(getattr(getattr(call, "function", None), "name", None)),
                    arguments=text(
                        getattr(getattr(call, "function", None), "arguments", None)
                    ),
                )
                for call in calls
            )
            if isinstance(calls, (list, tuple))
            else (),
            finish_reason=text(getattr(choice, "finish_reason", None)),
            provider_request_id=text(response.id),
            usage={
                key: value if type(value) is int else None
                for key, value in raw_usage.items()
            }
            if raw_usage is not None
            else None,
        )
        try:
            choice = response.choices[0]
            provider_message = choice.message

            tool_calls = [
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=self._parse_arguments(call.function.arguments),
                )
                for call in provider_message.tool_calls or []
            ]

            message = AssistantMessage(
                content=provider_message.content,
                tool_calls=tuple(tool_calls),
            )

            prompt_details = (
                response.usage.prompt_tokens_details if response.usage else None
            )
            completion_details = (
                response.usage.completion_tokens_details if response.usage else None
            )

            return ModelResponse(
                request_timing=timing,
                latency_ms=int(timing.client_duration_ms),
                message=message,
                finish_reason=self._map_finish_reason(choice.finish_reason),
                usage=ModelUsage(
                    input_tokens=response.usage.prompt_tokens,
                    cached_input_tokens=(
                        prompt_details.cached_tokens or 0 if prompt_details else 0
                    ),
                    output_tokens=response.usage.completion_tokens,
                    reasoning_output_tokens=(
                        completion_details.reasoning_tokens or 0
                        if completion_details
                        else 0
                    ),
                    total_tokens=response.usage.total_tokens,
                )
                if response.usage
                else None,
                provider_request_id=response.id,
            )
        except (ValueError, TypeError, IndexError, KeyError, AttributeError):
            raise ModelResponseNormalizationError(
                evidence=evidence, code="MODEL_RESPONSE_NORMALIZATION_FAILED"
            ) from None
