import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from miniagent.models.messages import UserMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProviderError,
    ModelRequest,
    ModelResponseNormalizationError,
)
from miniagent.providers.qwen import QwenProvider


@pytest.mark.parametrize(
    "variant", ["malformed", "array", "usage", "normal", "unknown_finish", "connection"]
)
async def test_tc_u06_01_and_04(variant: str) -> None:
    arguments = (
        "{broken" if variant == "malformed" else "[]" if variant == "array" else "{}"
    )
    payload = {
        "id": "request-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "final explanation",
                    "reasoning_content": "secret-thinking",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "shell", "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "alien"
                if variant == "unknown_finish"
                else "tool_calls",
            }
        ],
        "usage": {
            "prompt_tokens": 2,
            "completion_tokens": 10,
            "total_tokens": 1 if variant == "usage" else 12,
            "completion_tokens_details": {"reasoning_tokens": 6},
        },
    }

    def handler(request):
        if variant == "connection":
            raise httpx.ConnectError("connection unavailable", request=request)
        return httpx.Response(200, json=payload, headers={"x-secret": "secret-header"})

    provider = QwenProvider(api_key="secret-key", base_url="https://example.com/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="secret-key",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    request = ModelRequest(model="test", messages=(UserMessage(content="hello"),))
    try:
        if variant in {"malformed", "array", "usage"}:
            with pytest.raises(ModelProviderError) as exc:
                await provider.generate(request)
            assert isinstance(exc.value, ModelResponseNormalizationError)
            assert exc.value.evidence.usage is not None
            assert exc.value.evidence.content == "final explanation"
            assert exc.value.evidence.tool_calls[0].arguments == arguments
            assert exc.value.evidence.provider_request_id == "request-1"
            assert (
                exc.value.evidence.usage["total_tokens"]
                == payload["usage"]["total_tokens"]
            )
            assert "secret-thinking" not in exc.value.evidence.model_dump_json()
            assert "secret-header" not in str(exc.value)
            assert "secret-key" not in str(exc.value)
        elif variant == "connection":
            with pytest.raises(ModelProviderError) as exc:
                await provider.generate(request)
            assert not hasattr(exc.value, "evidence")
        else:
            response = await provider.generate(request)
            assert response.message.content == "final explanation"
            assert response.usage is not None
            assert response.usage.reasoning_output_tokens == 6
            assert response.finish_reason is (
                FinishReason.UNKNOWN
                if variant == "unknown_finish"
                else FinishReason.TOOL_CALL
            )
            assert "secret-thinking" not in response.model_dump_json()
    finally:
        await provider.client.close()
