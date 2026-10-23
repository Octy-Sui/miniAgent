from typing import Any, cast

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
    "variant",
    [
        "malformed",
        "array",
        "usage",
        "normal",
        "unknown_finish",
        "connection",
        "arguments_object",
        "numeric_id",
        "usage_shape",
    ],
)
async def test_tc_u06_01_and_04(variant: str) -> None:
    arguments = (
        "{broken" if variant == "malformed" else "[]" if variant == "array" else "{}"
    )
    payload: dict[str, Any] = {
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

    if variant == "arguments_object":
        cast(Any, payload)["choices"][0]["message"]["tool_calls"][0]["function"][
            "arguments"
        ] = {"value": "original"}
    elif variant == "numeric_id":
        cast(Any, payload)["choices"][0]["message"]["tool_calls"][0]["id"] = 42
    elif variant == "usage_shape":
        cast(Any, payload)["usage"]["prompt_tokens_details"] = "malformed"

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
        if variant in {
            "malformed",
            "array",
            "arguments_object",
            "numeric_id",
        }:
            with pytest.raises(ModelProviderError) as exc:
                await provider.generate(request)
            assert isinstance(exc.value, ModelResponseNormalizationError)
            assert exc.value.evidence.usage is not None
            assert exc.value.evidence.content == "final explanation"
            if variant != "arguments_object":
                assert exc.value.evidence.tool_calls[0].arguments == arguments
            assert exc.value.evidence.provider_request_id == "request-1"
            assert (
                exc.value.evidence.usage["total_tokens"]
                == payload["usage"]["total_tokens"]
            )
            assert "secret-thinking" not in exc.value.evidence.model_dump_json()
            assert "secret-header" not in str(exc.value)
            assert "secret-key" not in str(exc.value)
        elif variant in {"usage", "usage_shape"}:
            response = await provider.generate(request)
            assert response.message.content == "final explanation"
            assert response.usage is None
            assert response.usage_issue == "invalid"
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


@pytest.mark.parametrize("status", [400, 413, 429, 500])
@pytest.mark.parametrize("summary", [False, True])
async def test_http_errors_do_not_persist_sdk_error_bodies(tmp_path, status, summary):
    from datetime import UTC, datetime
    from time import monotonic

    from miniagent.artifacts import FileArtifactStore
    from miniagent.runtime import AgentRuntime, RuntimeConfig
    from miniagent.runtime.config import ModelRetryConfig
    from miniagent.storage import SQLiteRunStore
    from miniagent.tools.registry import ToolRegistry
    from miniagent.trajectory import TrajectoryDefinition, encode_trajectory
    from tests.support.runtime.compaction import pipeline
    from tests.support.runtime.runtime import agent

    sentinel = "forbidden-http-reasoning-and-credential"

    def handler(request):
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": "invalid_parameter",
                    "message": "provider rejected request",
                    "reasoning_content": sentinel,
                    "headers": {"Authorization": sentinel},
                }
            },
        )

    provider = QwenProvider(api_key="secret-key", base_url="https://example.com/v1")
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="secret-key",
        base_url="https://example.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    try:
        if summary:
            runtime, execution, _, store = await pipeline(tmp_path)
            runtime.provider = provider
            await runtime._prepare_request(execution, force=True)
            run_id = execution.state.identity.run_id
        else:
            store = SQLiteRunStore(tmp_path / "run.db")
            await store.initialize()
            result = await AgentRuntime(
                provider=provider,
                config=RuntimeConfig(
                    model_retry=ModelRetryConfig(max_consecutive_failures=1)
                ),
                tools=ToolRegistry([]),
                run_store=store,
                artifact_store=FileArtifactStore(tmp_path / "artifacts"),
            ).run(agent(), "task")
            run_id = result.run_id
        record = await store.get(run_id)
        document = await encode_trajectory(
            record=record,
            definition=TrajectoryDefinition(
                task=record.state.identity.task,
                agent=record.state.definition.agent_spec,
                tools=record.state.definition.tool_definitions,
                budgets=record.state.definition.config,
            ),
            store=store,
            artifacts=FileArtifactStore(tmp_path / "artifacts"),
            exported_at=datetime.now(UTC),
            producer_version="test",
            deadline=monotonic() + 10,
        )
        assert sentinel.encode() not in document
        assert sentinel not in record.model_dump_json()
        assert str(status).encode() in document
        assert all(
            sentinel not in event.model_dump_json()
            for event in await store.list_events(run_id)
        )
    finally:
        await provider.client.close()
