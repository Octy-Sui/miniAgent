# File   : qwen.py
# Created: 2026-09-09 17:02:08
# Author : octy Sui

from typing import Any

import orjson
from openai import AsyncOpenAI, APIConnectionError, APIStatusError, omit

from miniagent.models.messages import AssistantMessage, Message
from miniagent.models.protocol import (
    FinishReason,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ModelProviderError,
    ModelContextLimitError,
)
from miniagent.tools.contracts import ToolCall


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
                ] if request.tools else omit,
                temperature=request.generation.temperature,
                max_tokens=request.generation.max_output_tokens,
            )
        except APIStatusError as exc:
            code = str(exc.code or '').lower()
            message = str(exc).lower()
            if exc.status_code in (400, 413) and (code in {'context_length_exceeded', 'context_window_exceeded', 'inputtokensexceedlimit'} or 'maximum context length' in message):
                raise ModelContextLimitError(str(exc)) from exc
            if exc.status_code == 429 or exc.status_code >= 500:
                raise ModelProviderError(str(exc), retryable=True) from exc
            raise
        except APIConnectionError as exc:
            raise ModelProviderError(str(exc), retryable=True) from exc
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
