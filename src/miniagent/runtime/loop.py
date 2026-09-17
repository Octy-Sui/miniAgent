# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
import logging
import re
from collections.abc import Awaitable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.models import PriceConfig
from miniagent.models.model import Message, ModelRequest, ModelResponse
from miniagent.models.tool import ToolError, ToolResult, ToolStatus
from miniagent.policy import (
    ApprovalHandler,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    DefaultPolicyEngine,
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    build_policy_request,
    digest_json,
    make_policy_decision,
)
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
from miniagent.runtime.tool_output import render_tool_result
from miniagent.runtime.usage import add_request_usage, calculate_request_cost
from miniagent.tools.registry import PreparedToolCall, ToolRegistry

T = TypeVar("T")
_TOOL_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
logger = logging.getLogger(__name__)


class _CancellationRequested(Exception):
    pass


class AgentRuntime:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        tools: ToolRegistry,
        config: RuntimeConfig | None = None,
        cancellation_token: CancellationToken | None = None,
        evaluator: Evaluator | None = None,
        pricing: PriceConfig | None = None,
        policy_engine: PolicyEngine | None = None,
        policy_context: PolicyContext | None = None,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.config = config or RuntimeConfig()
        self.cancellation_token = cancellation_token or CancellationToken()
        self.evaluator = evaluator or PassThroughEvaluator()
        self.pricing = pricing
        self.policy_engine = policy_engine or DefaultPolicyEngine()
        self.policy_context = policy_context or PolicyContext(
            workspace_id="runtime",
            workspace_kind="local",
            revision="in-process-v1",
        )
        self.approval_handler = approval_handler

    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
    ) -> RunResult:
        self._validate_agent_tools(agent)
        if self.pricing is not None:
            self.pricing.price_for(agent.model.provider, agent.model.model)
        now = datetime.now(UTC)
        state = RunState(
            run_id=run_id or uuid4(),
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
                state.usage.model_requests += 1

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

                if self.pricing is not None:
                    if response.usage is None:
                        transition(
                            state,
                            RunStatus.FAILED,
                            reason=TerminationReason.MODEL_FAILED,
                            error=RunError(
                                code="MODEL_USAGE_MISSING",
                                message=(
                                    "Provider response did not include token usage"
                                ),
                            ),
                        )
                        return RunResult.from_state(state)

                    request_cost = calculate_request_cost(
                        provider=agent.model.provider,
                        model=agent.model.model,
                        usage=response.usage,
                        pricing=self.pricing,
                    )
                    state.usage = add_request_usage(
                        state.usage,
                        response.usage,
                        request_cost,
                    )

                state.messages.append(response.message)
                decision = evaluate_model_response(response)
                if decision is not None:
                    return await self._handle_model_decision(state, decision)

                state.pending_tool_calls = list(response.message.tool_calls)
                state.cursor = RunCursor.EXECUTE_TOOL_CALLS
                results = await self._execute_tool_calls(agent, response, state)
                if isinstance(results, RunResult):
                    return results
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
        try:
            self.tools.definitions(agent.allowed_tools)
        except ValueError as exc:
            raise ValueError(f"Invalid tools for agent {agent.name!r}: {exc}") from exc

    def _build_request(self, agent: AgentSpec, state: RunState) -> ModelRequest:
        return ModelRequest(
            model=agent.model.model,
            messages=state.messages,
            tools=self.tools.definitions(agent.allowed_tools),
            max_output_tokens=agent.max_output_tokens,
            temperature=agent.temperature,
        )

    async def _execute_tool_calls(
        self,
        agent: AgentSpec,
        response: ModelResponse,
        state: RunState,
    ) -> list[ToolResult] | RunResult:
        results: list[ToolResult] = []
        for call_index, call in enumerate(response.message.tool_calls):
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
                prepared = self.tools.prepare(call)
                if isinstance(prepared, ToolError):
                    result = ToolResult(
                        tool_call_id=call.id,
                        status=ToolStatus.ERROR,
                        content=f"Invalid arguments for {call.name}.",
                        error=prepared,
                    )
                else:
                    try:
                        decision = self._decide_policy(
                            agent,
                            state,
                            call_index,
                            call.id,
                            call.name,
                            prepared,
                        )
                    except Exception as exc:  # noqa: BLE001
                        transition(
                            state,
                            RunStatus.FAILED,
                            reason=TerminationReason.INTERNAL_ERROR,
                            error=RunError(
                                code="POLICY_EVALUATION_FAILED",
                                message=f"{type(exc).__name__}: {exc}",
                            ),
                        )
                        return RunResult.from_state(state)
                    if decision.effect is PolicyEffect.DENY:
                        result = self._policy_denied_result(call.id, decision)
                    elif decision.effect is PolicyEffect.APPROVAL:
                        approval_result = await self._request_approval(
                            state,
                            decision,
                        )
                        if isinstance(approval_result, RunResult):
                            return approval_result
                        if approval_result is not None:
                            result = approval_result
                        else:
                            state.usage.tool_calls += 1
                            result = await self._execute_tool(
                                call.id, call.name, prepared
                            )
                    else:
                        state.usage.tool_calls += 1
                        result = await self._execute_tool(call.id, call.name, prepared)

            results.append(result)
            state.messages.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    content=render_tool_result(
                        result,
                        self.config.tool_output_limits,
                    ),
                )
            )

        return results

    def _decide_policy(
        self,
        agent: AgentSpec,
        state: RunState,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
        prepared: PreparedToolCall,
    ) -> PolicyDecision:
        request = build_policy_request(
            run_id=state.run_id,
            step_number=state.steps_started,
            call_index=call_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=prepared.arguments,
            agent=agent,
            definition=self.tools.definition(tool_name),
            context=self.policy_context,
            tool_policy_metadata=self.tools.policy_metadata(tool_name),
        )
        decision = make_policy_decision(
            request,
            self.policy_engine,
            decided_at=datetime.now(UTC),
        )
        state.policy_decisions.append(decision)
        return decision

    async def _request_approval(
        self,
        state: RunState,
        decision: PolicyDecision,
    ) -> ToolResult | RunResult | None:
        if self.approval_handler is None:
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.APPROVAL_UNAVAILABLE,
                error=RunError(
                    code="APPROVAL_UNAVAILABLE",
                    message="The tool call requires approval, but no handler is configured.",
                ),
            )
            return RunResult.from_state(state)

        now = datetime.now(UTC)
        approval = ApprovalRequest(
            approval_id=uuid4(),
            decision_id=decision.decision_id,
            run_id=state.run_id,
            request_digest=decision.request_digest,
            policy_revision=decision.policy_revision,
            requested_at=now,
            expires_at=now + timedelta(seconds=self.config.approval_timeout_seconds),
        )
        state.approval_requests.append(approval)
        transition(
            state,
            RunStatus.WAITING_APPROVAL,
            pending_approval_id=str(approval.approval_id),
        )
        try:
            resolution = await self._await_with_cancellation(
                asyncio.wait_for(
                    self.approval_handler.resolve(approval, decision),
                    timeout=self.config.approval_timeout_seconds,
                )
            )
        except _CancellationRequested:
            raise
        except TimeoutError:
            resolution = ApprovalResolution(
                approval_id=approval.approval_id,
                outcome=ApprovalOutcome.EXPIRED,
                actor_kind="system",
                actor_id="runtime",
                reason="The approval request expired.",
                resolved_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            state.approval_resolutions.append(
                ApprovalResolution(
                    approval_id=approval.approval_id,
                    outcome=ApprovalOutcome.INVALIDATED,
                    actor_kind="system",
                    actor_id="runtime",
                    reason="The approval handler failed.",
                    resolved_at=datetime.now(UTC),
                )
            )
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.INTERNAL_ERROR,
                error=RunError(
                    code="APPROVAL_HANDLER_FAILED",
                    message=f"{type(exc).__name__}: {exc}",
                ),
                pending_approval_id=None,
            )
            return RunResult.from_state(state)
        if resolution.approval_id != approval.approval_id:
            state.approval_resolutions.append(
                ApprovalResolution(
                    approval_id=approval.approval_id,
                    outcome=ApprovalOutcome.INVALIDATED,
                    actor_kind="system",
                    actor_id="runtime",
                    reason="The approval handler returned a mismatched request ID.",
                    resolved_at=datetime.now(UTC),
                )
            )
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.INTERNAL_ERROR,
                error=RunError(
                    code="INVALID_APPROVAL_RESOLUTION",
                    message="Approval resolution does not match the pending request.",
                ),
                pending_approval_id=None,
            )
            return RunResult.from_state(state)

        if (
            datetime.now(UTC) >= approval.expires_at
            or resolution.resolved_at >= approval.expires_at
        ):
            resolution = ApprovalResolution(
                approval_id=approval.approval_id,
                outcome=ApprovalOutcome.EXPIRED,
                actor_kind="system",
                actor_id="runtime",
                reason="The approval request expired.",
                resolved_at=datetime.now(UTC),
            )
        state.approval_resolutions.append(resolution)
        outcome = resolution.outcome
        if outcome is not ApprovalOutcome.APPROVED:
            if outcome is ApprovalOutcome.REJECTED:
                reason = TerminationReason.APPROVAL_REJECTED
            elif outcome is ApprovalOutcome.EXPIRED:
                reason = TerminationReason.APPROVAL_EXPIRED
            else:
                reason = TerminationReason.INTERNAL_ERROR
            transition(
                state,
                RunStatus.FAILED,
                reason=reason,
                error=RunError(
                    code=reason.value.upper(),
                    message=f"Tool approval was {outcome.value}.",
                ),
                pending_approval_id=None,
            )
            return RunResult.from_state(state)

        transition(
            state,
            RunStatus.RUNNING,
            cursor=RunCursor.EXECUTE_TOOL_CALLS,
            pending_approval_id=None,
        )
        if (
            self.policy_engine.revision != decision.policy_revision
            or digest_json(decision.request.model_dump(mode="json"))
            != decision.request_digest
        ):
            return self._policy_denied_result(
                decision.request.tool_call_id,
                decision.model_copy(
                    update={
                        "reason_code": "APPROVAL_INVALIDATED",
                        "reason": "The approved policy request changed before execution.",
                    }
                ),
            )
        try:
            evaluation = self.policy_engine.evaluate(decision.request)
        except Exception as exc:  # noqa: BLE001
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.INTERNAL_ERROR,
                error=RunError(
                    code="POLICY_EVALUATION_FAILED",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
            return RunResult.from_state(state)
        if evaluation.effect is PolicyEffect.DENY:
            changed = make_policy_decision(
                decision.request,
                self.policy_engine,
                decided_at=datetime.now(UTC),
            )
            return self._policy_denied_result(
                decision.request.tool_call_id,
                changed,
            )
        return None

    @staticmethod
    def _policy_denied_result(
        tool_call_id: str,
        decision: PolicyDecision,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.DENIED,
            content=f"Tool call denied by policy: {decision.reason}",
            error=ToolError(
                code="POLICY_DENIED",
                message=decision.reason_code,
            ),
        )

    async def _execute_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        prepared: PreparedToolCall,
    ) -> ToolResult:
        try:
            result = await self._await_with_cancellation(
                prepared.tool.execute(
                    prepared.arguments,
                    tool_call_id=tool_call_id,
                )
            )
        except _CancellationRequested:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Tool execution failed", extra={"tool": tool_name})
            return ToolResult(
                tool_call_id=tool_call_id,
                status=ToolStatus.ERROR,
                content=f"Tool execution failed: {tool_name}",
                error=ToolError(
                    code="TOOL_EXECUTION_EXCEPTION",
                    message="Tool execution raised an unexpected exception.",
                ),
            )

        if not isinstance(result, ToolResult):
            return self._invalid_tool_result(
                tool_call_id,
                tool_name,
                "Tool returned an unsupported result type.",
            )
        if result.tool_call_id != tool_call_id:
            return self._invalid_tool_result(
                tool_call_id,
                tool_name,
                "Tool result does not match the current call.",
            )
        if result.error is not None and not _TOOL_ERROR_CODE.fullmatch(
            result.error.code
        ):
            return self._invalid_tool_result(
                tool_call_id,
                tool_name,
                "Tool returned an invalid error code.",
            )
        return result

    @staticmethod
    def _invalid_tool_result(
        tool_call_id: str,
        tool_name: str,
        message: str,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.ERROR,
            content=f"Invalid result from tool: {tool_name}",
            error=ToolError(code="INVALID_TOOL_RESULT", message=message),
        )

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
        if state.pending_approval_id is not None:
            state.approval_resolutions.append(
                ApprovalResolution(
                    approval_id=UUID(state.pending_approval_id),
                    outcome=ApprovalOutcome.INVALIDATED,
                    actor_kind="system",
                    actor_id="runtime",
                    reason="The run was cancelled.",
                    resolved_at=datetime.now(UTC),
                )
            )
        transition(
            state,
            RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
            pending_approval_id=None,
        )
        return RunResult.from_state(state)
