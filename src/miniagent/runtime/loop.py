# File   : loop.py
# Created: 2026-09-10 16:35:13
# Author : octy Sui

import asyncio
import logging
import re
import secrets
from collections.abc import Awaitable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from pydantic import JsonValue

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
from miniagent.runtime.events import NewRunEvent, RunEventType
from miniagent.runtime.persistence import RunRecoveryContext
from miniagent.runtime.recording import RunPersistenceError, RunRecorder, RuntimeStore
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
from miniagent.storage import (
    ApprovalExpiredError,
    CreateApproval,
    InvalidateApproval,
    ResolveApproval,
)
from miniagent.tools.registry import PreparedToolCall, ToolRegistry

T = TypeVar("T")
_TOOL_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
logger = logging.getLogger(__name__)


class _CancellationRequested(Exception):
    pass


@dataclass(slots=True)
class _RunExecution:
    state: RunState
    recorder: RunRecorder
    run_span_id: str
    step_span_id: str | None = None
    active_tool_span_id: str | None = None
    active_tool_call_index: int | None = None
    active_tool_call_id: str | None = None
    active_tool_name: str | None = None
    active_approval_span_id: str | None = None
    active_tool_execution_span_id: str | None = None
    tool_execution_active: bool = False


def _span_id() -> str:
    return secrets.token_hex(8)


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
        run_store: RuntimeStore | None = None,
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
        self.run_store = run_store

    async def run(
        self,
        agent: AgentSpec,
        task: str,
        *,
        run_id: UUID | None = None,
        recovery_context: RunRecoveryContext | None = None,
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
        execution = _RunExecution(
            state=state,
            recorder=RunRecorder(
                run_id=state.run_id,
                store=self.run_store,
                recovery_context=recovery_context,
            ),
            run_span_id=_span_id(),
        )
        await execution.recorder.create(state)
        transition(state, RunStatus.PREPARING)
        transition(state, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)
        await execution.recorder.commit(
            state,
            events=[
                self._event(
                    execution,
                    RunEventType.RUN_STARTED,
                    span_id=execution.run_span_id,
                ),
            ],
            snapshot=True,
        )

        try:
            while True:
                if self.cancellation_token.is_cancelled:
                    return await self._cancelled_result(execution)

                if state.steps_started >= self.config.max_steps:
                    transition(
                        state,
                        RunStatus.FAILED,
                        reason=TerminationReason.MAX_STEPS_REACHED,
                    )
                    return await self._finish(execution)

                state.steps_started += 1
                state.usage.model_requests += 1
                execution.step_span_id = _span_id()
                model_span_id = _span_id()
                await execution.recorder.commit(
                    state,
                    events=[
                        self._event(
                            execution,
                            RunEventType.STEP_STARTED,
                            step=True,
                        ),
                        self._event(
                            execution,
                            RunEventType.MODEL_REQUESTED,
                            payload={
                                "provider": agent.model.provider,
                                "model": agent.model.model,
                            },
                            span_id=model_span_id,
                            parent_span_id=execution.step_span_id,
                        ),
                    ],
                )

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
                    return await self._finish(execution)

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
                        return await self._finish(execution)

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
                    await execution.recorder.commit(
                        state,
                        events=[
                            self._event(
                                execution,
                                RunEventType.MODEL_RESPONDED,
                                payload={
                                    "finish_reason": response.finish_reason.value,
                                    "tool_call_count": len(response.message.tool_calls),
                                },
                                span_id=model_span_id,
                                parent_span_id=execution.step_span_id,
                            )
                        ],
                    )
                    return await self._handle_model_decision(execution, decision)

                state.pending_tool_calls = list(response.message.tool_calls)
                state.cursor = RunCursor.EXECUTE_TOOL_CALLS
                await execution.recorder.commit(
                    state,
                    events=[
                        self._event(
                            execution,
                            RunEventType.MODEL_RESPONDED,
                            payload={
                                "finish_reason": response.finish_reason.value,
                                "tool_call_count": len(response.message.tool_calls),
                            },
                            span_id=model_span_id,
                            parent_span_id=execution.step_span_id,
                        )
                    ],
                )
                results = await self._execute_tool_calls(agent, response, execution)
                if isinstance(results, RunResult):
                    return results
                state.pending_tool_calls = []
                state.cursor = RunCursor.REQUEST_MODEL
                if tool_step_failed(results):
                    state.consecutive_failed_steps += 1
                else:
                    state.consecutive_failed_steps = 0

                await execution.recorder.commit(
                    state,
                    events=[
                        self._event(
                            execution,
                            RunEventType.STEP_COMPLETED,
                            payload={
                                "failed": tool_step_failed(results),
                                "consecutive_failed_steps": (
                                    state.consecutive_failed_steps
                                ),
                            },
                            step=True,
                        )
                    ],
                    snapshot=True,
                )
                execution.step_span_id = None

                if (
                    state.consecutive_failed_steps
                    >= self.config.max_consecutive_failures
                ):
                    transition(
                        state,
                        RunStatus.FAILED,
                        reason=TerminationReason.CONSECUTIVE_FAILURE_LIMIT,
                    )
                    return await self._finish(execution)
        except _CancellationRequested:
            return await self._cancelled_result(execution)

    def _event(
        self,
        execution: _RunExecution,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
        step: bool = False,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> NewRunEvent:
        in_step = (
            execution.step_span_id is not None
            and event_type is not RunEventType.RUN_FINISHED
        )
        return NewRunEvent(
            run_id=execution.state.run_id,
            type=event_type,
            payload=payload or {},
            step_number=(
                execution.state.steps_started if step or in_step else None
            ),
            span_id=(execution.step_span_id if step else span_id),
            parent_span_id=(
                execution.run_span_id
                if step and parent_span_id is None
                else parent_span_id
            ),
        )

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
        execution: _RunExecution,
    ) -> list[ToolResult] | RunResult:
        state = execution.state
        results: list[ToolResult] = []
        for call_index, call in enumerate(response.message.tool_calls):
            if self.cancellation_token.is_cancelled:
                raise _CancellationRequested
            await self._record_tool_start(
                execution,
                call_index=call_index,
                tool_call_id=call.id,
                tool_name=call.name,
            )

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
                        return await self._finish(execution)
                    if decision.effect is PolicyEffect.DENY:
                        result = self._policy_denied_result(call.id, decision)
                    elif decision.effect is PolicyEffect.APPROVAL:
                        approval_result = await self._request_approval(
                            execution,
                            decision,
                        )
                        if isinstance(approval_result, RunResult):
                            return approval_result
                        if approval_result is not None:
                            result = approval_result
                        else:
                            state.usage.tool_calls += 1
                            await self._record_tool_execution_start(execution)
                            result = await self._execute_tool(
                                call.id, call.name, prepared
                            )
                    else:
                        state.usage.tool_calls += 1
                        await self._record_tool_execution_start(execution)
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
            state.completed_tool_call_ids.add(call.id)
            await self._record_tool_end(execution, result)
        return results

    async def _record_tool_start(
        self,
        execution: _RunExecution,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        execution.active_tool_span_id = _span_id()
        execution.active_tool_call_index = call_index
        execution.active_tool_call_id = tool_call_id
        execution.active_tool_name = tool_name
        await execution.recorder.commit(
            execution.state,
            events=[
                self._event(
                    execution,
                    RunEventType.TOOL_START,
                    payload={
                        "call_index": call_index,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                    },
                    span_id=execution.active_tool_span_id,
                    parent_span_id=execution.step_span_id,
                )
            ],
        )

    async def _record_tool_execution_start(
        self,
        execution: _RunExecution,
    ) -> None:
        execution.active_tool_execution_span_id = _span_id()
        await execution.recorder.commit(
            execution.state,
            events=[
                self._active_tool_execution_event(
                    execution,
                    RunEventType.TOOL_EXECUTION_START,
                )
            ],
        )
        execution.tool_execution_active = True

    async def _record_tool_end(
        self,
        execution: _RunExecution,
        result: ToolResult,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "status": result.status.value,
            "error_code": result.error.code if result.error is not None else None,
        }
        events: list[NewRunEvent] = []
        if execution.tool_execution_active:
            events.append(
                self._active_tool_execution_event(
                    execution,
                    RunEventType.TOOL_EXECUTION_END,
                    payload=payload,
                )
            )
        events.append(
            self._active_tool_event(
                execution,
                RunEventType.TOOL_END,
                payload=payload,
            )
        )
        await execution.recorder.commit(execution.state, events=events)
        self._clear_active_tool(execution)

    def _active_tool_execution_event(
        self,
        execution: _RunExecution,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        if execution.active_tool_execution_span_id is None:
            raise RuntimeError("Tool execution event requires an active execution")
        return self._active_tool_child_event(
            execution,
            event_type,
            span_id=execution.active_tool_execution_span_id,
            payload=payload,
        )

    def _active_approval_event(
        self,
        execution: _RunExecution,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        if execution.active_approval_span_id is None:
            raise RuntimeError("Approval event requires an active approval")
        return self._active_tool_child_event(
            execution,
            event_type,
            span_id=execution.active_approval_span_id,
            payload=payload,
        )

    def _active_tool_child_event(
        self,
        execution: _RunExecution,
        event_type: RunEventType,
        *,
        span_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        if execution.active_tool_span_id is None:
            raise RuntimeError("Tool child event requires an active tool call")
        tool = self._active_tool_event(execution, event_type, payload=payload)
        return tool.model_copy(
            update={
                "span_id": span_id,
                "parent_span_id": execution.active_tool_span_id,
            }
        )

    def _active_tool_event(
        self,
        execution: _RunExecution,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        if (
            execution.active_tool_span_id is None
            or execution.active_tool_call_index is None
            or execution.active_tool_call_id is None
            or execution.active_tool_name is None
        ):
            raise RuntimeError("Tool lifecycle event requires an active tool call")
        return self._event(
            execution,
            event_type,
            payload={
                "call_index": execution.active_tool_call_index,
                "tool_call_id": execution.active_tool_call_id,
                "tool_name": execution.active_tool_name,
                **(payload or {}),
            },
            span_id=execution.active_tool_span_id,
            parent_span_id=execution.step_span_id,
        )

    @staticmethod
    def _clear_active_tool(execution: _RunExecution) -> None:
        execution.active_tool_span_id = None
        execution.active_tool_call_index = None
        execution.active_tool_call_id = None
        execution.active_tool_name = None
        execution.active_approval_span_id = None
        execution.active_tool_execution_span_id = None
        execution.tool_execution_active = False

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
        execution: _RunExecution,
        decision: PolicyDecision,
    ) -> ToolResult | RunResult | None:
        state = execution.state
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
            return await self._finish(execution)

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
        execution.active_approval_span_id = _span_id()
        transition(
            state,
            RunStatus.WAITING_APPROVAL,
            pending_approval_id=str(approval.approval_id),
        )
        opened = await execution.recorder.commit(
            state,
            events=[
                self._active_approval_event(
                    execution,
                    RunEventType.APPROVAL_REQUESTED,
                    payload={
                        "approval_id": str(approval.approval_id),
                        "decision_id": str(decision.decision_id),
                    },
                ),
            ],
            approval_changes=[
                CreateApproval(
                    request=approval,
                    decision=decision,
                    request_event_index=0,
                )
            ],
            snapshot=True,
        )
        approval_version = opened.approvals[0].version if opened is not None else 1
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
            return await self._finish(
                execution,
                approval_resolution=state.approval_resolutions[-1],
                approval_version=approval_version,
            )
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
            return await self._finish(
                execution,
                approval_resolution=state.approval_resolutions[-1],
                approval_version=approval_version,
            )

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
            return await self._finish(
                execution,
                approval_resolution=resolution,
                approval_version=approval_version,
            )

        transition(
            state,
            RunStatus.RUNNING,
            cursor=RunCursor.EXECUTE_TOOL_CALLS,
            pending_approval_id=None,
        )
        try:
            resolved = await execution.recorder.commit(
                state,
                events=[
                    self._active_approval_event(
                        execution,
                        RunEventType.APPROVAL_RESOLVED,
                        payload={
                            "approval_id": str(approval.approval_id),
                            "outcome": resolution.outcome.value,
                        },
                    ),
                ],
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=approval_version,
                        resolution_event_index=0,
                    )
                ],
            )
        except RunPersistenceError as exc:
            if not isinstance(exc.__cause__, ApprovalExpiredError):
                raise
            if execution.recorder.record is None:
                raise RuntimeError("Approval expiry requires persisted run state") from exc
            state = execution.recorder.record.state.model_copy(deep=True)
            execution.state = state
            expired = ApprovalResolution(
                approval_id=approval.approval_id,
                outcome=ApprovalOutcome.EXPIRED,
                actor_kind="system",
                actor_id="runtime",
                reason="The approval request expired during persistence.",
                resolved_at=datetime.now(UTC),
            )
            state.approval_resolutions.append(expired)
            transition(
                state,
                RunStatus.FAILED,
                reason=TerminationReason.APPROVAL_EXPIRED,
                error=RunError(
                    code="APPROVAL_EXPIRED",
                    message="Tool approval expired before it could be committed.",
                ),
                pending_approval_id=None,
            )
            return await self._finish(
                execution,
                approval_resolution=expired,
                approval_version=approval_version,
            )
        execution.active_approval_span_id = None
        approved_version = resolved.approvals[0].version if resolved is not None else 2
        if (
            self.policy_engine.revision != decision.policy_revision
            or digest_json(decision.request.model_dump(mode="json"))
            != decision.request_digest
        ):
            await self._invalidate_approval(
                execution,
                approval.approval_id,
                expected_version=approved_version,
                reason="policy_request_changed",
            )
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
            return await self._finish(execution)
        if evaluation.effect is PolicyEffect.DENY:
            changed = make_policy_decision(
                decision.request,
                self.policy_engine,
                decided_at=datetime.now(UTC),
            )
            await self._invalidate_approval(
                execution,
                approval.approval_id,
                expected_version=approved_version,
                reason="policy_no_longer_allows",
            )
            return self._policy_denied_result(
                decision.request.tool_call_id,
                changed,
            )
        return None

    async def _invalidate_approval(
        self,
        execution: _RunExecution,
        approval_id: UUID,
        *,
        expected_version: int,
        reason: str,
    ) -> None:
        await execution.recorder.commit(
            execution.state,
            events=[
                self._event(
                    execution,
                    RunEventType.APPROVAL_INVALIDATED,
                    payload={
                        "approval_id": str(approval_id),
                        "reason": reason,
                    },
                )
            ],
            approval_changes=[
                InvalidateApproval(
                    approval_id=approval_id,
                    expected_version=expected_version,
                    invalidated_event_index=0,
                )
            ],
        )

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
        execution: _RunExecution,
        decision: ModelDecision,
    ) -> RunResult:
        state = execution.state
        if decision.target is RunStatus.FAILED:
            if decision.reason is None:
                raise ValueError("Failed model decision requires a reason")
            transition(
                state,
                RunStatus.FAILED,
                reason=decision.reason,
                error=decision.error,
            )
            return await self._finish(execution)

        if decision.target is not RunStatus.EVALUATING:
            raise ValueError(f"Unsupported model decision: {decision.target}")
        if decision.candidate_output is None:
            raise ValueError("Evaluation decision requires candidate output")

        state.candidate_output = decision.candidate_output
        transition(state, RunStatus.EVALUATING)
        await execution.recorder.commit(
            state,
            events=[
                self._event(
                    execution,
                    RunEventType.STEP_COMPLETED,
                    payload={"failed": False, "candidate_output": True},
                    step=True,
                ),
            ],
            snapshot=True,
        )
        execution.step_span_id = None

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
            return await self._finish(execution)

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
        return await self._finish(
            execution,
            preceding_events=[
                self._event(
                    execution,
                    RunEventType.EVALUATION_COMPLETED,
                    payload={"passed": evaluation.passed},
                )
            ],
        )

    async def _cancelled_result(self, execution: _RunExecution) -> RunResult:
        state = execution.state
        approval_resolution = None
        approval_version = None
        if state.pending_approval_id is not None:
            approval_resolution = ApprovalResolution(
                approval_id=UUID(state.pending_approval_id),
                outcome=ApprovalOutcome.INVALIDATED,
                actor_kind="system",
                actor_id="runtime",
                reason="The run was cancelled.",
                resolved_at=datetime.now(UTC),
            )
            state.approval_resolutions.append(approval_resolution)
            if execution.recorder.store is not None:
                approval_version = (
                    await execution.recorder.store.get_approval(
                        approval_resolution.approval_id
                    )
                ).version
        transition(
            state,
            RunStatus.CANCELLED,
            reason=TerminationReason.CANCEL_REQUESTED,
            pending_approval_id=None,
        )
        return await self._finish(
            execution,
            approval_resolution=approval_resolution,
            approval_version=approval_version,
        )

    async def _finish(
        self,
        execution: _RunExecution,
        *,
        approval_resolution: ApprovalResolution | None = None,
        approval_version: int | None = None,
        preceding_events: Sequence[NewRunEvent] = (),
    ) -> RunResult:
        state = execution.state
        events = list(preceding_events)
        approval_changes = []
        if approval_resolution is not None:
            if approval_version is None:
                raise ValueError("Approval resolution requires its stored version")
            events.append(
                self._active_approval_event(
                    execution,
                    RunEventType.APPROVAL_RESOLVED,
                    payload={
                        "approval_id": str(approval_resolution.approval_id),
                        "outcome": approval_resolution.outcome.value,
                    },
                )
            )
            approval_changes.append(
                ResolveApproval(
                    resolution=approval_resolution,
                    expected_version=approval_version,
                    resolution_event_index=len(events) - 1,
                )
            )
        finish_active_tool = execution.active_tool_span_id is not None
        if finish_active_tool:
            tool_payload: dict[str, JsonValue] = {
                "status": state.status.value,
                "reason": (
                    state.termination_reason.value
                    if state.termination_reason is not None
                    else None
                ),
            }
            if execution.tool_execution_active:
                events.append(
                    self._active_tool_execution_event(
                        execution,
                        RunEventType.TOOL_EXECUTION_END,
                        payload=tool_payload,
                    )
                )
            events.append(
                self._active_tool_event(
                    execution,
                    RunEventType.TOOL_END,
                    payload=tool_payload,
                )
            )
        finish_step = execution.step_span_id is not None
        if finish_step:
            events.append(
                self._event(
                    execution,
                    RunEventType.STEP_COMPLETED,
                    payload={"failed": state.status is not RunStatus.SUCCEEDED},
                    step=True,
                )
            )
        events.append(
            self._event(
                execution,
                RunEventType.RUN_FINISHED,
                payload={
                    "status": state.status.value,
                    "reason": (
                        state.termination_reason.value
                        if state.termination_reason is not None
                        else None
                    ),
                },
                span_id=execution.run_span_id,
            )
        )
        await execution.recorder.commit(
            state,
            events=events,
            approval_changes=approval_changes,
            snapshot=True,
        )
        if finish_active_tool:
            self._clear_active_tool(execution)
        elif approval_resolution is not None:
            execution.active_approval_span_id = None
        if finish_step:
            execution.step_span_id = None
        return RunResult.from_state(state)
