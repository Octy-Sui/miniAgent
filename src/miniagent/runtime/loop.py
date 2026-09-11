# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import suppress
from typing import TypeVar

from miniagent.agents import AgentSpec
from miniagent.models.model import Message, ModelRequest, ModelResponse
from miniagent.models.tool import ToolError, ToolResult, ToolStatus
from miniagent.providers import ModelProvider
from miniagent.runtime.cancellation import CancellationToken
from miniagent.runtime.state import (
    RunError,
    RunResult,
    RunState,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
)
from miniagent.runtime.termination import (
    TerminationDecision,
    evaluate_model_response,
    tool_step_failed,
)
from miniagent.tools import Tool

T = TypeVar("T")


class _CancellationRequested(Exception):
    pass


class AgentRuntime:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: Mapping[str, Tool],
        config: RuntimeConfig | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        self.provider = provider
        self._tools = dict(tools)
        self.config = config or RuntimeConfig()
        self.cancellation_token = cancellation_token or CancellationToken()

    async def run(self, agent: AgentSpec, task: str) -> RunResult:
        self._validate_agent_tools(agent)
        state = RunState(
            messages=[
                Message(role="system", content=agent.system_prompt),
                Message(role="user", content=task),
            ]
        )

        try:
            while True:
                if self.cancellation_token.is_cancelled:
                    return self._cancelled_result(state)

                if state.steps_started >= self.config.max_steps:
                    return self._result(
                        state,
                        status=RunStatus.FAILED,
                        reason=TerminationReason.MAX_STEPS_REACHED,
                    )

                state.steps_started += 1

                try:
                    response = await self._await_with_cancellation(
                        self.provider.generate(self._build_request(agent, state))
                    )
                except _CancellationRequested:
                    raise
                except Exception as exc:  # noqa: BLE001
                    return self._result(
                        state,
                        status=RunStatus.FAILED,
                        reason=TerminationReason.MODEL_FAILED,
                        error=RunError(
                            code="PROVIDER_EXCEPTION",
                            message=f"{type(exc).__name__}: {exc}",
                        ),
                    )

                state.messages.append(response.message)
                decision = evaluate_model_response(response)
                if decision is not None:
                    return self._decision_result(state, decision)

                results = await self._execute_tool_calls(agent, response, state)
                if tool_step_failed(results):
                    state.consecutive_failures += 1
                else:
                    state.consecutive_failures = 0

                if state.consecutive_failures >= self.config.max_consecutive_failures:
                    return self._result(
                        state,
                        status=RunStatus.FAILED,
                        reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
                    )
        except _CancellationRequested:
            return self._cancelled_result(state)

    def _validate_agent_tools(self, agent: AgentSpec) -> None:
        unknown_tools = set(agent.allowed_tools) - self._tools.keys()
        if unknown_tools:
            names = ", ".join(sorted(unknown_tools))
            raise ValueError(f"Agent {agent.name!r} references unknown tools: {names}")

    def _build_request(self, agent: AgentSpec, state: RunState) -> ModelRequest:
        return ModelRequest(
            model=agent.model.model,
            messages=state.messages,
            tools=[self._tools[name].definition for name in agent.allowed_tools],
            max_output_tokens=agent.max_output_tokens,
            temperature=agent.temperature,
        )

    async def _execute_tool_calls(
        self,
        agent: AgentSpec,
        response: ModelResponse,
        state: RunState,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for call in response.message.tool_calls:
            if self.cancellation_token.is_cancelled:
                raise _CancellationRequested

            if call.name not in agent.allowed_tools:
                result = ToolResult(
                    tool_call_id=call.id,
                    status=ToolStatus.DENIED,
                    content=f"Tool not allowed for agent {agent.name}: {call.name}",
                    error=ToolError(code="TOOL_NOT_ALLOWED", message=call.name),
                )
            else:
                tool = self._tools[call.name]
                try:
                    result = await self._await_with_cancellation(
                        tool.execute(call.arguments, tool_call_id=call.id)
                    )
                except _CancellationRequested:
                    raise
                except Exception as exc:  # noqa: BLE001
                    result = ToolResult(
                        tool_call_id=call.id,
                        status=ToolStatus.ERROR,
                        content=f"Tool execution failed: {call.name}",
                        error=ToolError(
                            code="TOOL_EXECUTION_EXCEPTION",
                            message=f"{type(exc).__name__}: {exc}",
                        ),
                    )

            results.append(result)
            state.messages.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    content=result.content,
                )
            )

        return results

    async def _await_with_cancellation(self, awaitable: Awaitable[T]) -> T:
        if self.cancellation_token.is_cancelled:
            if asyncio.iscoroutine(awaitable):
                awaitable.close()
            raise _CancellationRequested

        operation = asyncio.ensure_future(awaitable)
        cancellation = asyncio.create_task(self.cancellation_token.wait())

        try:
            done, _ = await asyncio.wait(
                {operation, cancellation},
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Operation完成和Cancellation同时发生时，
            # 优先接受已经完成的结果。
            if operation in done:
                return operation.result()

            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation

            raise _CancellationRequested

        finally:
            for task in (operation, cancellation):
                if not task.done():
                    task.cancel()

            for task in (operation, cancellation):
                with suppress(asyncio.CancelledError):
                    await task

    def _decision_result(
        self,
        state: RunState,
        decision: TerminationDecision,
    ) -> RunResult:
        return self._result(
            state,
            status=decision.status,
            reason=decision.reason,
            output=decision.output,
            error=decision.error,
        )

    def _cancelled_result(self, state: RunState) -> RunResult:
        return self._result(
            state,
            status=RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
        )

    @staticmethod
    def _result(
        state: RunState,
        *,
        status: RunStatus,
        reason: TerminationReason,
        output: str | None = None,
        error: RunError | None = None,
    ) -> RunResult:
        return RunResult(
            status=status,
            reason=reason,
            output=output,
            steps=state.steps_started,
            consecutive_failures=state.consecutive_failures,
            messages=state.messages,
            error=error,
        )
