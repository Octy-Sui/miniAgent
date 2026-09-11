# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeVar
from uuid import uuid4

from miniagent.agents import AgentSpec
from miniagent.models.model import Message, ModelRequest, ModelResponse
from miniagent.models.tool import ToolError, ToolResult, ToolStatus
from miniagent.providers import ModelProvider
from miniagent.runtime.cancellation import CancellationToken
from miniagent.runtime.evaluator import (
    EvaluationRequest,
    Evaluator,
    PassThroughEvaluator,
)
from miniagent.runtime.state import (
    RunCursor,
    RunError,
    RunResult,
    RunState,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
    transition,
)
from miniagent.runtime.termination import (
    ModelDecision,
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
        evaluator: Evaluator | None = None,
    ) -> None:
        self.provider = provider
        self._tools = dict(tools)
        self.config = config or RuntimeConfig()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.evaluator = evaluator or PassThroughEvaluator()

    async def run(self, agent: AgentSpec, task: str) -> RunResult:
        self._validate_agent_tools(agent)
        now = datetime.now(UTC)
        state = RunState(
            run_id=uuid4(),
            task=task,
            agent_spec=agent,
            config=self.config,
            messages=[
                Message(role="system", content=agent.system_prompt),
                Message(role="user", content=task),
            ],
            created_at=now,
            updated_at=now,
        )
        transition(state, RunStatus.PREPARING)
        transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)

        try:
            while True:
                if self.cancellation_token.is_cancelled:
                    return self._cancelled_result(state)

                if state.steps_started >= self.config.max_steps:
                    transition(
                        state,
                        RunStatus.FAILED,
                        reason=TerminationReason.MAX_STEPS_REACHED,
                    )
                    return RunResult.from_state(state)

                state.steps_started += 1

                try:
                    response = await self._await_with_cancellation(
                        self.provider.generate(self._build_request(agent, state))
                    )
                except _CancellationRequested:
                    raise
                except Exception as exc:  # noqa: BLE001
                    transition(
                        state,
                        RunStatus.FAILED,
                        reason=TerminationReason.MODEL_FAILED,
                        error=RunError(
                            code="PROVIDER_EXCEPTION",
                            message=f"{type(exc).__name__}: {exc}",
                        ),
                    )
                    return RunResult.from_state(state)

                state.messages.append(response.message)
                decision = evaluate_model_response(response)
                if decision is not None:
                    return await self._handle_model_decision(state, decision)

                state.pending_tool_calls = list(response.message.tool_calls)
                state.cursor = RunCursor.EXECUTE_TOOL_CALLS
                results = await self._execute_tool_calls(agent, response, state)
                state.pending_tool_calls = []
                state.cursor = RunCursor.REQUEST_MODEL
                if tool_step_failed(results):
                    state.consecutive_failed_steps += 1
                else:
                    state.consecutive_failed_steps = 0

                if (
                    state.consecutive_failed_steps
                    >= self.config.max_consecutive_failures
                ):
                    transition(
                        state,
                        RunStatus.FAILED,
                        reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
                    )
                    return RunResult.from_state(state)
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

    async def _handle_model_decision(
        self,
        state: RunState,
        decision: ModelDecision,
    ) -> RunResult:
        if decision.target is RunStatus.FAILED:
            if decision.reason is None:
                raise ValueError("Failed model decision requires a reason")
            transition(
                state,
                RunStatus.FAILED,
                reason=decision.reason,
                error=decision.error,
            )
            return RunResult.from_state(state)

        if decision.target is not RunStatus.EVALUATING:
            raise ValueError(f"Unsupported model decision: {decision.target}")
        if decision.candidate_output is None:
            raise ValueError("Evaluation decision requires candidate output")

        state.candidate_output = decision.candidate_output
        transition(state, RunStatus.EVALUATING)

        try:
            evaluation = await self._await_with_cancellation(
                self.evaluator.evaluate(
                    EvaluationRequest(
                        run_id=state.run_id,
                        task=state.task,
                        candidate_output=state.candidate_output,
                    )
                )
            )
        except _CancellationRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.EVALUATION_FAILED,
                error=RunError(
                    code="EVALUATOR_EXCEPTION",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
            return RunResult.from_state(state)

        if evaluation.passed:
            state.final_output = (
                evaluation.output
                if evaluation.output is not None
                else state.candidate_output
            )
            transition(
                state,
                RunStatus.SUCCEEDED,
                reason=TerminationReason.TASK_VERIFIED,
            )
        else:
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.EVALUATION_FAILED,
                error=evaluation.error,
            )
        return RunResult.from_state(state)

    def _cancelled_result(self, state: RunState) -> RunResult:
        transition(
            state,
            RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
        )
        return RunResult.from_state(state)
