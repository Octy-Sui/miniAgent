import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from miniagent.agents import AgentSpec
from miniagent.models.tool import ToolCall, ToolError, ToolResult, ToolStatus
from miniagent.policy import (
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    build_policy_request,
    make_policy_decision,
)
from miniagent.runtime.approval import ApprovalCoordinator
from miniagent.runtime.cancellation import (
    CancellationRequested,
    CancellationToken,
    await_with_cancellation,
)
from miniagent.runtime.execution import RunExecution, RunTermination
from miniagent.runtime.state import RunError, RunStatus, TerminationReason
from miniagent.tools.registry import PreparedToolCall, ToolRegistry

_TOOL_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ToolBatchOutcome:
    results: list[ToolResult]
    termination: RunTermination | None = None


class ToolCallRunner:
    def __init__(
        self,
        *,
        tools: ToolRegistry,
        policy_engine: PolicyEngine,
        policy_context: PolicyContext,
        approval: ApprovalCoordinator,
        cancellation_token: CancellationToken,
    ) -> None:
        self.tools = tools
        self.policy_engine = policy_engine
        self.policy_context = policy_context
        self.approval = approval
        self.cancellation_token = cancellation_token

    async def execute(
        self,
        execution: RunExecution,
        agent: AgentSpec,
        calls: Sequence[ToolCall],
    ) -> ToolBatchOutcome:
        results: list[ToolResult] = []
        for call_index, call in enumerate(calls):
            if self.cancellation_token.is_cancelled:
                raise CancellationRequested
            await execution.begin_tool(
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
                            execution,
                            agent,
                            call_index,
                            call,
                            prepared,
                        )
                    except Exception as exc:  # noqa: BLE001
                        return ToolBatchOutcome(
                            results=results,
                            termination=RunTermination(
                                status=RunStatus.FAILED,
                                reason=TerminationReason.INTERNAL_ERROR,
                                error=RunError(
                                    code="POLICY_EVALUATION_FAILED",
                                    message=f"{type(exc).__name__}: {exc}",
                                ),
                            ),
                        )
                    if decision.effect is PolicyEffect.DENY:
                        result = self._policy_denied_result(call.id, decision)
                    elif decision.effect is PolicyEffect.APPROVAL:
                        authorization = await self.approval.authorize(
                            execution,
                            decision,
                        )
                        if authorization.termination is not None:
                            return ToolBatchOutcome(
                                results=results,
                                termination=authorization.termination,
                            )
                        if authorization.tool_result is not None:
                            result = authorization.tool_result
                        elif authorization.authorized:
                            result = await self._execute_prepared(
                                execution,
                                call,
                                prepared,
                            )
                        else:
                            raise RuntimeError(
                                "Approval coordinator returned no authorization outcome"
                            )
                    else:
                        result = await self._execute_prepared(
                            execution,
                            call,
                            prepared,
                        )

            results.append(result)
            await execution.complete_tool(result)
        return ToolBatchOutcome(results=results)

    def _decide_policy(
        self,
        execution: RunExecution,
        agent: AgentSpec,
        call_index: int,
        call: ToolCall,
        prepared: PreparedToolCall,
    ) -> PolicyDecision:
        request = build_policy_request(
            run_id=execution.state.run_id,
            step_number=execution.state.steps_started,
            call_index=call_index,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=prepared.arguments,
            agent=agent,
            definition=self.tools.definition(call.name),
            context=self.policy_context,
            tool_policy_metadata=self.tools.policy_metadata(call.name),
        )
        decision = make_policy_decision(
            request,
            self.policy_engine,
            decided_at=datetime.now(UTC),
        )
        execution.record_policy_decision(decision)
        return decision

    async def _execute_prepared(
        self,
        execution: RunExecution,
        call: ToolCall,
        prepared: PreparedToolCall,
    ) -> ToolResult:
        await execution.begin_tool_execution()
        try:
            result = await await_with_cancellation(
                prepared.tool.execute(
                    prepared.arguments,
                    tool_call_id=call.id,
                ),
                self.cancellation_token,
            )
        except CancellationRequested:
            raise
        except Exception:
            logger.exception("Tool execution failed", extra={"tool": call.name})
            return ToolResult(
                tool_call_id=call.id,
                status=ToolStatus.ERROR,
                content=f"Tool execution failed: {call.name}",
                error=ToolError(
                    code="TOOL_EXECUTION_EXCEPTION",
                    message="Tool execution raised an unexpected exception.",
                ),
            )

        if not isinstance(result, ToolResult):
            return self._invalid_tool_result(
                call.id,
                call.name,
                "Tool returned an unsupported result type.",
            )
        if result.tool_call_id != call.id:
            return self._invalid_tool_result(
                call.id,
                call.name,
                "Tool result does not match the current call.",
            )
        if result.error is not None and not _TOOL_ERROR_CODE.fullmatch(
            result.error.code
        ):
            return self._invalid_tool_result(
                call.id,
                call.name,
                "Tool returned an invalid error code.",
            )
        return result

    @staticmethod
    def _policy_denied_result(
        tool_call_id: str,
        decision: PolicyDecision,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.DENIED,
            content=f"Tool call denied by policy: {decision.reason}",
            error=ToolError(code="POLICY_DENIED", message=decision.reason_code),
        )

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
