import json

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI, BadRequestError

from miniagent.models.messages import UserMessage
from miniagent.models.protocol import ModelContextLimitError, ModelRequest
from miniagent.providers.qwen import QwenProvider


@pytest.mark.parametrize(
    ("code", "message", "mapped"),
    [
        ("context_length_exceeded", "too long", True),
        ("invalid_parameter", "maximum context length exceeded", True),
        ("invalid_parameter", "max_tokens must be positive", False),
        ("invalid_parameter", "bad tool schema", False),
    ],
)
async def test_qwen_maps_only_context_limit_errors(code, message, mapped) -> None:
    def handler(request):
        return httpx.Response(400, json={"error": {"code": code, "message": message}})

    provider = QwenProvider(api_key="test", base_url="https://example.com/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        with pytest.raises(ModelContextLimitError if mapped else BadRequestError):
            await provider.generate(
                ModelRequest(model="test", messages=(UserMessage(content="hello"),))
            )
    finally:
        await provider.client.close()


async def test_empty_tools_wire_payload_has_no_callable_tools() -> None:
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "request-1",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            },
        )

    provider = QwenProvider(api_key="test", base_url="https://example.com/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        await provider.generate(
            ModelRequest(
                model="test", messages=(UserMessage(content="hello"),), tools=()
            )
        )
        assert "tools" not in payloads[0]
        assert "tool_choice" not in payloads[0]
    finally:
        await provider.client.close()
