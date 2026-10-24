import re
from dataclasses import dataclass
from datetime import UTC, datetime

from miniagent.agents import AgentSpec
from miniagent.artifacts.contracts import ArtifactPersistenceError, ArtifactRef
from miniagent.diagnostics import log_event
from miniagent.diagnostics import operation as observe_operation
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    execution_scope,
)
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
    await_with_stop,
)
from miniagent.runtime.execution import RunExecution, RunTermination
from miniagent.runtime.interruption import (
    InterruptionRequested,
    current_interruption_token,
    raise_if_interrupted,
)
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.runtime.state import RunStatus, ToolBatch
from miniagent.tools.contracts import (
    ToolCall,
    ToolError,
    ToolResult,
    ToolResultEvidence,
    ToolReturnValidation,
    ToolStatus,
)
from miniagent.tools.registry import PreparedToolCall, ToolRegistry

_TOOL_ERROR_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")


@dataclass(frozen=True, slots=True)
class CapturedToolReturn:
    raw_result: ToolResultEvidence | None
    effective_result: ToolResult
    validation: ToolReturnValidation
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolBatchOutcome:
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
        batch: ToolBatch | None,
    ) -> ToolBatchOutcome:
        if batch is None:
            raise ValueError("Tool execution requires an active batch")
        for call_index in range(batch.next_index, len(batch.calls)):
            raise_if_interrupted()
            call = batch.calls[call_index]
            captured = None
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
                    data={
                        "text": f"Tool not allowed for agent {agent.name}: {call.name}"
                    },
                    error=ToolError(code="TOOL_NOT_ALLOWED", message=call.name),
                )
            else:
                prepared = self.tools.prepare(call)
                if isinstance(prepared, ToolError):
                    result = ToolResult(
                        tool_call_id=call.id,
                        status=ToolStatus.ERROR,
                        data={"text": f"Invalid arguments for {call.name}."},
                        error=prepared,
                    )
                else:
                    try:
                        decision = await self._decide_policy(
                            execution,
                            agent,
                            call_index,
                            call,
                            prepared,
                        )
                    except (
                        RunPersistenceError,
                        ManagedExecutionError,
                        ArtifactPersistenceError,
                    ):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        return ToolBatchOutcome(
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
                                termination=authorization.termination,
                            )
                        if authorization.tool_result is not None:
                            result = authorization.tool_result
                        elif authorization.authorized:
                            captured = await self._execute_prepared(
                                execution,
                                call,
                                prepared,
                            )
                        else:
                            raise RuntimeError(
                                "Approval coordinator returned no authorization outcome"
                            )
                    else:
                        captured = await self._execute_prepared(
                            execution,
                            call,
                            prepared,
                        )

            if captured is not None:
                result = captured.effective_result
            termination = await execution.complete_tool(
                result,
                raw_result=captured.raw_result if captured else None,
                return_validation=captured.validation
                if captured
                else ToolReturnValidation(
                    disposition="not_returned", reason="tool_not_executed"
                ),
                artifacts=captured.artifacts if captured else tuple(result.artifacts),
            )
            if termination is not None:
                return ToolBatchOutcome(termination=termination)
        return ToolBatchOutcome()

    async def _decide_policy(
        self,
        execution: RunExecution,
        agent: AgentSpec,
        call_index: int,
        call: ToolCall,
        prepared: PreparedToolCall,
    ) -> PolicyDecision:
        request = build_policy_request(
            run_id=execution.state.identity.run_id,
            step_number=execution.state.execution.steps_started,
            call_index=call_index,
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=prepared.canonical_arguments,
            agent=agent,
            definition=self.tools.definition(call.name),
            context=self.policy_context,
            tool_contract_metadata=self.tools.contract_metadata(call.name),
        )
        decision = make_policy_decision(
            request,
            self.policy_engine,
            decided_at=datetime.now(UTC),
        )
        await execution.record_policy_decision(decision)
        return decision

    async def _execute_prepared(
        self,
        execution: RunExecution,
        call: ToolCall,
        prepared: PreparedToolCall,
    ) -> CapturedToolReturn:
        raise_if_interrupted()
        await execution.begin_tool_execution(
            canonical_arguments=prepared.canonical_arguments
        )
        try:
            operation = execution.state.execution.active_operation
            assert operation is not None
            with execution_scope(execution.execution_observer(operation.attempt_id)):
                async with observe_operation(
                    "tool",
                    lifecycle=False,
                    step_number=execution.state.execution.steps_started,
                    attempt_id=operation.attempt_id,
                    tool_name=call.name,
                ):
                    result = await await_with_stop(
                        prepared.invoke(call.id),
                        self.cancellation_token,
                        current_interruption_token(),
                    )
        except (
            CancellationRequested,
            InterruptionRequested,
            RunPersistenceError,
            ArtifactPersistenceError,
            ManagedExecutionError,
        ):
            raise
        except Exception:  # noqa: BLE001 -- translate unexpected tool failures.
            log_event(
                "tool.exception",
                tool_name=call.name,
                error_code="TOOL_EXECUTION_EXCEPTION",
            )
            effective = ToolResult(
                tool_call_id=call.id,
                status=ToolStatus.ERROR,
                data={"text": f"Tool execution failed: {call.name}"},
                error=ToolError(
                    code="TOOL_EXECUTION_EXCEPTION",
                    message="Tool execution raised an unexpected exception.",
                ),
            )
            return CapturedToolReturn(
                None,
                effective,
                ToolReturnValidation(
                    disposition="not_returned", reason="tool_exception"
                ),
            )

        if not isinstance(result, ToolResult):
            return CapturedToolReturn(
                None,
                self._invalid_tool_result(
                    call.id, call.name, "Tool returned an unsupported result type."
                ),
                ToolReturnValidation(
                    disposition="rejected", reason="unsupported_result_type"
                ),
            )
        result = result.model_copy(deep=True)
        artifacts = tuple(result.artifacts)
        if result.tool_call_id != call.id:
            return CapturedToolReturn(
                await execution.capture_tool_result(result),
                self._invalid_tool_result(
                    call.id, call.name, "Tool result does not match the current call."
                ),
                ToolReturnValidation(
                    disposition="rejected", reason="tool_call_id_mismatch"
                ),
                artifacts,
            )
        if result.error is not None and not _TOOL_ERROR_CODE.fullmatch(
            result.error.code
        ):
            return CapturedToolReturn(
                await execution.capture_tool_result(result),
                self._invalid_tool_result(
                    call.id, call.name, "Tool returned an invalid error code."
                ),
                ToolReturnValidation(
                    disposition="rejected", reason="invalid_error_code"
                ),
                artifacts,
            )
        return CapturedToolReturn(
            None, result, ToolReturnValidation(disposition="accepted"), artifacts
        )

    @staticmethod
    def _policy_denied_result(
        tool_call_id: str,
        decision: PolicyDecision,
    ) -> ToolResult:
        return ToolResult(
            tool_call_id=tool_call_id,
            status=ToolStatus.DENIED,
            data={"text": f"Tool call denied by policy: {decision.reason}"},
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
            data={"text": f"Invalid result from tool: {tool_name}"},
            error=ToolError(code="INVALID_TOOL_RESULT", message=message),
        )
