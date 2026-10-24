import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from miniagent.policy import (
    ApprovalHandler,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
    PolicyEffect,
    PolicyEngine,
    digest_json,
    make_policy_decision,
)
from miniagent.runtime.cancellation import (
    CancellationRequested,
    CancellationToken,
    await_with_stop,
)
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.execution import RunExecution, RunTermination
from miniagent.runtime.interruption import (
    InterruptionRequested,
    current_interruption_token,
    raise_if_interrupted,
)
from miniagent.runtime.journal import ApprovalCommitExpired
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.runtime.state import RunStatus
from miniagent.tools.contracts import ToolError, ToolResult, ToolStatus


@dataclass(frozen=True, slots=True)
class ApprovalAuthorization:
    authorized: bool = False
    tool_result: ToolResult | None = None
    termination: RunTermination | None = None


class ApprovalCoordinator:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        handler: ApprovalHandler | None,
        policy_engine: PolicyEngine,
        cancellation_token: CancellationToken,
    ) -> None:
        self.config = config
        self.handler = handler
        self.policy_engine = policy_engine
        self.cancellation_token = cancellation_token

    async def authorize(
        self,
        execution: RunExecution,
        decision: PolicyDecision,
    ) -> ApprovalAuthorization:
        if self.handler is None:
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.APPROVAL_UNAVAILABLE,
                    error=RunError(
                        code="APPROVAL_UNAVAILABLE",
                        message=(
                            "The tool call requires approval, but no handler is "
                            "configured."
                        ),
                    ),
                )
            )

        raise_if_interrupted()
        now = datetime.now(UTC)
        request = ApprovalRequest(
            approval_id=uuid4(),
            decision_id=decision.decision_id,
            run_id=execution.state.identity.run_id,
            request_digest=decision.request_digest,
            policy_revision=decision.policy_revision,
            requested_at=now,
            expires_at=now + timedelta(seconds=self.config.approval_timeout_seconds),
        )
        await execution.open_approval(request, decision)
        try:
            resolution = await await_with_stop(
                asyncio.wait_for(
                    self.handler.resolve(request, decision),
                    timeout=self.config.approval_timeout_seconds,
                ),
                self.cancellation_token,
                current_interruption_token(),
            )
        except (CancellationRequested, InterruptionRequested):
            raise
        except TimeoutError:
            resolution = self._system_resolution(
                request,
                ApprovalOutcome.EXPIRED,
                "The approval request expired.",
            )
        except Exception as exc:  # noqa: BLE001
            resolution = self._system_resolution(
                request,
                ApprovalOutcome.INVALIDATED,
                "The approval handler failed.",
            )
            execution.stage_approval_resolution(resolution)
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.INTERNAL_ERROR,
                    error=RunError(
                        code="APPROVAL_HANDLER_FAILED",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                )
            )

        if resolution.approval_id != request.approval_id:
            invalidated = self._system_resolution(
                request,
                ApprovalOutcome.INVALIDATED,
                "The approval handler returned a mismatched request ID.",
            )
            execution.stage_approval_resolution(invalidated)
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.INTERNAL_ERROR,
                    error=RunError(
                        code="INVALID_APPROVAL_RESOLUTION",
                        message=(
                            "Approval resolution does not match the pending request."
                        ),
                    ),
                )
            )

        if (
            datetime.now(UTC) >= request.expires_at
            or resolution.resolved_at >= request.expires_at
        ):
            resolution = self._system_resolution(
                request,
                ApprovalOutcome.EXPIRED,
                "The approval request expired.",
            )

        if resolution.outcome is not ApprovalOutcome.APPROVED:
            execution.stage_approval_resolution(resolution)
            if resolution.outcome is ApprovalOutcome.REJECTED:
                reason = TerminationReason.APPROVAL_REJECTED
            elif resolution.outcome is ApprovalOutcome.EXPIRED:
                reason = TerminationReason.APPROVAL_EXPIRED
            else:
                reason = TerminationReason.INTERNAL_ERROR
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=reason,
                    error=RunError(
                        code=reason.value.upper(),
                        message=f"Tool approval was {resolution.outcome.value}.",
                    ),
                )
            )

        try:
            await execution.resolve_approval(resolution)
        except ApprovalCommitExpired:
            expired = self._system_resolution(
                request,
                ApprovalOutcome.EXPIRED,
                "The approval request expired during persistence.",
            )
            execution.stage_approval_resolution(expired)
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.APPROVAL_EXPIRED,
                    error=RunError(
                        code="APPROVAL_EXPIRED",
                        message="Tool approval expired before it could be committed.",
                    ),
                )
            )

        if (
            self.policy_engine.revision != decision.policy_revision
            or digest_json(decision.request.model_dump(mode="json"))
            != decision.request_digest
        ):
            await execution.invalidate_approval(
                request.approval_id,
                reason="policy_request_changed",
            )
            return ApprovalAuthorization(
                tool_result=self._denied_result(
                    decision.model_copy(
                        update={
                            "reason_code": "APPROVAL_INVALIDATED",
                            "reason": (
                                "The approved policy request changed before execution."
                            ),
                        }
                    )
                )
            )

        try:
            evaluation = self.policy_engine.evaluate(decision.request)
        except Exception as exc:  # noqa: BLE001
            return ApprovalAuthorization(
                termination=RunTermination(
                    status=RunStatus.FAILED,
                    reason=TerminationReason.INTERNAL_ERROR,
                    error=RunError(
                        code="POLICY_EVALUATION_FAILED",
                        message=f"{type(exc).__name__}: {exc}",
                    ),
                )
            )
        if evaluation.effect is PolicyEffect.DENY:
            changed = make_policy_decision(
                decision.request,
                self.policy_engine,
                decided_at=datetime.now(UTC),
            )
            await execution.invalidate_approval(
                request.approval_id,
                reason="policy_no_longer_allows",
            )
            return ApprovalAuthorization(tool_result=self._denied_result(changed))
        return ApprovalAuthorization(authorized=True)

    @staticmethod
    def _system_resolution(
        request: ApprovalRequest,
        outcome: ApprovalOutcome,
        reason: str,
    ) -> ApprovalResolution:
        return ApprovalResolution(
            approval_id=request.approval_id,
            outcome=outcome,
            actor_kind="system",
            actor_id="runtime",
            reason=reason,
            resolved_at=datetime.now(UTC),
        )

    @staticmethod
    def _denied_result(decision: PolicyDecision) -> ToolResult:
        return ToolResult(
            tool_call_id=decision.request.tool_call_id,
            status=ToolStatus.DENIED,
            data={"text": f"Tool call denied by policy: {decision.reason}"},
            error=ToolError(code="POLICY_DENIED", message=decision.reason_code),
        )
