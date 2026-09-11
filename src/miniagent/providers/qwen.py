# File   : qwen.py
# Created: 2026-09-09 17:02:08
# Author : octy Sui

from typing import Any

import orjson
from openai import AsyncOpenAI

from miniagent.models.model import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
    Usage,
)
from miniagent.models.tool import ToolCall
from miniagent.providers import ModelProvider


class QwenProvider(ModelProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str,
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
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
            ],
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
        )
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

        message = Message(
            role="assistant",
            content=provider_message.content,
            tool_calls=tool_calls,
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
            usage=Usage(
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
