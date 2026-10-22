import asyncio

import httpx2 as httpx
import pytest
from openai import AsyncOpenAI, BadRequestError

from miniagent.models.messages import UserMessage
from miniagent.models.protocol import ModelProviderError, ModelRequest
from miniagent.providers.qwen import QwenProvider


@pytest.mark.parametrize("status", [200, 400, 500])
async def test_request_timing_separates_client_rt_and_provider_duration(status):
    async def handler(request):
        await asyncio.sleep(0.02)
        metadata = {
            "duration": "1613",
            "request_id": "provider-request",
            "start_unix_timestamp": "1792586368025",
            "status_code": "0",
            "apikey_id": "must-not-record",
            "client_ip": "must-not-record",
        }
        if status != 200:
            return httpx.Response(
                status,
                json={
                    **metadata,
                    "error": {"code": "invalid_parameter", "message": "rejected"},
                },
            )
        return httpx.Response(
            200,
            json={
                **metadata,
                "id": "completion-id",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    provider = QwenProvider(api_key="test", base_url="https://example.test/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        request = ModelRequest(model="test", messages=(UserMessage(content="hi"),))
        if status == 200:
            result = await provider.generate(request)
            timing = result.request_timing
            assert result.latency_ms is not None
        else:
            with pytest.raises(
                BadRequestError if status == 400 else ModelProviderError
            ) as caught:
                await provider.generate(request)
            timing = getattr(caught.value, "request_timing", None)
        assert timing is not None
        assert timing.client_duration_ms >= 10
        assert timing.server_duration_ms == 1613
        assert timing.provider_request_id == "provider-request"
        assert "must-not-record" not in timing.model_dump_json()
    finally:
        await provider.client.close()
