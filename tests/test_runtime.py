# File   : test_runtime.py
# Created: 2026-09-10 18:14:00
# Author : octy Sui

import asyncio
import unittest
from typing import Any

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.models.model import (
    FinishReason,
    Message,
    ModelRequest,
    ModelResponse,
)
from miniagent.models.tool import ToolCall, ToolDefinition, ToolResult, ToolStatus
from miniagent.providers import ModelProvider
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
)
from miniagent.tools import Tool


class QueueProvider(ModelProvider):
    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BlockingProvider(ModelProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class SuccessfulTool(Tool):
    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="ok", description="Succeed", input_schema={})

    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        tool_call_id: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.SUCCESS,
            content="done",
        )


def final_response(content: str = "finished") -> ModelResponse:
    return ModelResponse(
        message=Message(role="assistant", content=content),
        finish_reason=FinishReason.STOP,
    )


def tool_response(name: str = "ok") -> ModelResponse:
    return ModelResponse(
        message=Message(
            role="assistant",
            tool_calls=[ToolCall(id="call", name=name, arguments={})],
        ),
        finish_reason=FinishReason.TOOL_CALL,
    )


def agent(*, allowed_tools: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="test",
        system_prompt="Test system prompt.",
        model=ModelTarget(provider="test", model="test-model"),
        allowed_tools=allowed_tools or [],
        temperature=0.25,
        max_output_tokens=100,
    )


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_stop_completes_run(self) -> None:
        runtime = AgentRuntime(
            provider=QueueProvider([final_response()]),
            tools={},
        )

        result = await runtime.run(agent(), "task")

        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.reason, TerminationReason.MODEL_COMPLETED)
        self.assertEqual(result.output, "finished")
        self.assertEqual(result.steps, 1)

    async def test_step_limit_stops_tool_loop(self) -> None:
        provider = QueueProvider([tool_response(), tool_response()])
        runtime = AgentRuntime(
            provider=provider,
            tools={"ok": SuccessfulTool()},
            config=RuntimeConfig(max_steps=2),
        )

        result = await runtime.run(agent(allowed_tools=["ok"]), "task")

        self.assertEqual(result.status, RunStatus.FAILED)
        self.assertEqual(result.reason, TerminationReason.MAX_STEPS_REACHED)
        self.assertEqual(result.steps, 2)
        self.assertEqual(provider.requests[0].model, "test-model")
        self.assertEqual(provider.requests[0].temperature, 0.25)
        self.assertEqual(provider.requests[0].max_output_tokens, 100)
        self.assertEqual([tool.name for tool in provider.requests[0].tools], ["ok"])

    async def test_abnormal_model_finish_fails_run(self) -> None:
        response = ModelResponse(
            message=Message(role="assistant", content="partial"),
            finish_reason=FinishReason.LENGTH,
        )
        runtime = AgentRuntime(
            provider=QueueProvider([response]),
            tools={},
        )

        result = await runtime.run(agent(), "task")

        self.assertEqual(result.reason, TerminationReason.MODEL_FAILED)
        assert result.error is not None
        self.assertEqual(result.error.code, "MODEL_ABNORMAL_FINISH")

    async def test_provider_exception_fails_run(self) -> None:
        runtime = AgentRuntime(
            provider=QueueProvider([RuntimeError("offline")]),
            tools={},
        )

        result = await runtime.run(agent(), "task")

        self.assertEqual(result.reason, TerminationReason.MODEL_FAILED)
        assert result.error is not None
        self.assertEqual(result.error.code, "PROVIDER_EXCEPTION")

    async def test_consecutive_failed_tool_steps_stop_run(self) -> None:
        runtime = AgentRuntime(
            provider=QueueProvider(
                [tool_response("missing"), tool_response("missing")]
            ),
            tools={},
            config=RuntimeConfig(max_consecutive_failures=2),
        )

        result = await runtime.run(agent(), "task")

        self.assertEqual(
            result.reason,
            TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
        )
        self.assertEqual(result.consecutive_failures, 2)

    async def test_cancellation_stops_in_flight_model_request(self) -> None:
        provider = BlockingProvider()
        token = CancellationToken()
        runtime = AgentRuntime(
            provider=provider,
            tools={},
            cancellation_token=token,
        )
        run = asyncio.create_task(runtime.run(agent(), "task"))
        await provider.started.wait()

        token.cancel()
        result = await asyncio.wait_for(run, timeout=1)

        self.assertEqual(result.status, RunStatus.CANCELLED)
        self.assertEqual(result.reason, TerminationReason.CANCEL_REQUESTED)
        self.assertTrue(provider.cancelled)

    async def test_asyncio_cancellation_propagates_and_stops_model(self) -> None:
        provider = BlockingProvider()
        runtime = AgentRuntime(provider=provider, tools={})
        run = asyncio.create_task(runtime.run(agent(), "task"))
        await provider.started.wait()

        run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await run

        self.assertTrue(provider.cancelled)


if __name__ == "__main__":
    unittest.main()
