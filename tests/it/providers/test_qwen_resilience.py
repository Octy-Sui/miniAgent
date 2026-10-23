from contextlib import asynccontextmanager

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI

from miniagent.models.messages import UserMessage
from miniagent.models.protocol import ModelProviderError, ModelRequest
from miniagent.providers.qwen import QwenProvider


@asynccontextmanager
async def provider_for(handler):
    provider = QwenProvider(api_key="secret", base_url="https://example.test/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="secret",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        yield provider
    finally:
        await provider.client.close()


def request():
    return ModelRequest(model="test", messages=(UserMessage(content="task"),))


@pytest.mark.parametrize(
    ("status", "code", "category", "retryable"),
    [
        (429, "Throttling.AllocationQuota", "rate_limit", True),
        (429, "BudgetLimitExceeded", "permanent", False),
        (500, "InternalError", "server", True),
        (504, "GatewayTimeout", "gateway", True),
        (401, "InvalidApiKey", "permanent", False),
    ],
)
async def test_provider_preserves_retry_classification(
    status, code, category, retryable
):
    def handler(req):
        return httpx.Response(
            status,
            json={"error": {"code": code, "message": "secret"}},
            headers={"retry-after": "17", "x-request-id": "request-id"},
        )

    async with provider_for(handler) as provider:
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request())
    failure = caught.value.failure
    assert failure.category == category
    assert failure.status_code == status
    assert failure.retryable is retryable
    assert failure.retry_after_seconds == 17
    assert caught.value.request_timing is not None
    assert caught.value.request_timing.provider_request_id == "request-id"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (httpx.ConnectTimeout, "connection"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.ConnectError, "connection"),
        (httpx.WriteError, "connection"),
    ],
)
async def test_transport_failures_retain_safe_type(exception, category):
    def handler(req):
        raise exception("secret-url", request=req)

    async with provider_for(handler) as provider:
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request())
    assert caught.value.failure.category == category
    assert caught.value.failure.exception_type == exception.__name__
    assert caught.value.retryable
    assert "secret-url" not in str(caught.value)


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": False},
        },
        {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "completion_tokens_details": {"reasoning_tokens": ""},
        },
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": -1},
        {
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "prompt_tokens_details": "bad",
        },
    ],
)
async def test_invalid_usage_preserves_executable_response(usage):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "id": "response",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "answer",
                            "tool_calls": [
                                {
                                    "id": "call",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command":"true"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": usage,
            },
        )

    async with provider_for(handler) as provider:
        result = await provider.generate(request())
    assert result.message.content == "answer"
    assert result.message.tool_calls[0].arguments == {"command": "true"}
    assert result.usage is None
    assert result.usage_issue == ("missing" if usage is None else "invalid")


async def test_enormous_retry_after_is_preserved_as_a_stop_budget_signal():
    def handler(req):
        return httpx.Response(
            429,
            json={"error": {"code": "Throttling.RateQuota", "message": "limited"}},
            headers={"retry-after": "1e100"},
        )

    async with provider_for(handler) as provider:
        with pytest.raises(ModelProviderError) as caught:
            await provider.generate(request())
    assert caught.value.failure.retry_after_seconds == 1e100
