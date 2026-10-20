from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import ValidationError

from miniagent.environments.attachment import AttachmentFacts
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    ExecutionEvidence,
    ExecutionIdentity,
)
from miniagent.policy.approvals import ApprovalOutcome
from miniagent.runtime.events import (
    DiagnosticFinalizationCompleted,
    DiagnosticFinalizationExecutionEnded,
    DiagnosticFinalizationExecutionIdentified,
    DiagnosticFinalizationInterrupted,
    DiagnosticFinalizationStarted,
    OperationExecutionEnded,
    OperationExecutionIdentified,
    OperationInterrupted,
    RunEvent,
)
from miniagent.runtime.history import iter_run_events
from miniagent.runtime.progress import ComponentEnvelope, ExecutionPhase
from miniagent.runtime.state import TERMINAL_STATUSES, RunState, RunStatus
from miniagent.runtime.store_contracts import (
    RunRecord,
    RuntimeStore,
    StoredApproval,
    ToolResultFact,
)


@dataclass(frozen=True, slots=True)
class ResumeRejection:
    code: str
    run_id: UUID
    message: str
    phase: ExecutionPhase | None = None
    source_sequence: int | None = None
    step_number: int | None = None
    call_index: int | None = None
    tool_call_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionChainFact:
    attempt_id: UUID
    identities: tuple[ExecutionIdentity, ...]
    create_permission_committed: bool
    start_permission_committed: bool
    command_permission_committed: bool
    evidence: tuple[ExecutionEvidence, ...]
    scope: Literal["business", "diagnostic"]


@dataclass(frozen=True, slots=True)
class ResumeHistoryFacts:
    tool_results: tuple[ToolResultFact, ...] = ()
    approvals: tuple[StoredApproval, ...] = ()
    interrupted_attempt_ids: frozenset[UUID] = frozenset()
    execution_chains: tuple[ExecutionChainFact, ...] = ()
    history_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResumeEnvironmentFacts:
    attachment: AttachmentFacts
    execution_evidence: tuple[ExecutionEvidence, ...] = ()
    dependency_rejections: tuple[ResumeRejection, ...] = ()
    candidate_rejection: ResumeRejection | None = None
    evaluation_rejection: ResumeRejection | None = None


@dataclass(frozen=True, slots=True)
class ResumePlan:
    run_id: UUID
    source_sequence: int
    target_status: Literal["preparing", "running", "evaluating"]
    phase: ExecutionPhase
    close_operations: tuple[UUID, ...] = ()
    invalidate_approval_ids: tuple[UUID, ...] = ()
    live_checks: tuple[str, ...] = ()
    cleanup_requirements: tuple[ComponentEnvelope, ...] = ()
    diagnostics: tuple[str, ...] = ()
    readiness: Literal["ready", "requires_live_checks"] = "ready"


class _HistoryFactsBuilder:
    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self.expected_sequence = 1
        self.operation_found = record.state.execution.active_operation is None
        self.errors: list[str] = []
        self.identities: dict[UUID, dict[UUID, ExecutionIdentity]] = {}
        self.ended: dict[UUID, dict[UUID, ExecutionEvidence]] = {}
        self.permissions: dict[UUID, set[str]] = {}
        self.interrupted: set[UUID] = set()
        self.diagnostic_attempts: set[UUID] = set()

    def feed(self, event: RunEvent) -> None:
        if (
            event.sequence != self.expected_sequence
            or event.sequence > self.record.last_event_sequence
        ):
            self.errors.append("Event history is incomplete or out of order")
        self.expected_sequence = event.sequence + 1
        if event.run_id != self.record.state.identity.run_id:
            self.errors.append("Event history belongs to another run")
        operation = self.record.state.execution.active_operation
        if (
            operation is not None
            and event.event_id == operation.started_event_id
            and event.sequence == operation.started_sequence
        ):
            self.operation_found = True
        payload = event.payload
        if isinstance(payload, DiagnosticFinalizationStarted):
            self.diagnostic_attempts.add(payload.attempt_id)
            self.identities.setdefault(payload.attempt_id, {})
            self.permissions.setdefault(payload.attempt_id, set())
        if isinstance(
            payload,
            (OperationExecutionIdentified, DiagnosticFinalizationExecutionIdentified),
        ):
            self.identities.setdefault(payload.attempt_id, {})[
                payload.identity.execution_id
            ] = payload.identity
            self.permissions.setdefault(payload.attempt_id, set()).add(
                payload.permission
            )
        elif isinstance(
            payload, (OperationExecutionEnded, DiagnosticFinalizationExecutionEnded)
        ):
            if (
                self.identities.get(payload.attempt_id, {}).get(
                    payload.evidence.execution_id
                )
                != payload.evidence.identity
            ):
                self.errors.append("Execution end has no matching identified fact")
            self.ended.setdefault(payload.attempt_id, {})[
                payload.evidence.execution_id
            ] = payload.evidence
        elif isinstance(payload, OperationInterrupted):
            self.interrupted.add(payload.attempt_id)
        elif (
            isinstance(payload, DiagnosticFinalizationCompleted)
            or isinstance(payload, DiagnosticFinalizationInterrupted)
            and payload.execution_ended
        ):
            known = self.identities.get(payload.attempt_id, {})
            proof = self.ended.get(payload.attempt_id, {})
            if any(
                identity.execution_id not in proof
                or proof[identity.execution_id].identity != identity
                or proof[identity.execution_id].outcome != "ended"
                or not proof[identity.execution_id].group_ended
                or not proof[identity.execution_id].issuer_ended
                for identity in known.values()
            ):
                self.errors.append("Diagnostic completion lacks whole-group end facts")
            self.interrupted.add(payload.attempt_id)

    def finish(
        self,
        *,
        tool_results: tuple[ToolResultFact, ...] = (),
        approvals: tuple[StoredApproval, ...] = (),
    ) -> ResumeHistoryFacts:
        if self.expected_sequence != self.record.last_event_sequence + 1:
            self.errors.append("Event history is incomplete or out of order")
        if not self.operation_found:
            self.errors.append("Active operation start fact is missing")
        chains = tuple(
            ExecutionChainFact(
                attempt_id=attempt,
                identities=tuple(items.values()),
                create_permission_committed="create_exec" in self.permissions[attempt],
                start_permission_committed="start_exec" in self.permissions[attempt],
                command_permission_committed="observe" in self.permissions[attempt],
                evidence=tuple(self.ended.get(attempt, {}).values()),
                scope="diagnostic"
                if attempt in self.diagnostic_attempts
                else "business",
            )
            for attempt, items in self.identities.items()
        )
        return ResumeHistoryFacts(
            tool_results,
            approvals,
            frozenset(self.interrupted),
            chains,
            tuple(self.errors),
        )


def history_facts(
    record: RunRecord,
    events: Iterable[RunEvent],
    *,
    tool_results: tuple[ToolResultFact, ...] = (),
    approvals: tuple[StoredApproval, ...] = (),
) -> ResumeHistoryFacts:
    builder = _HistoryFactsBuilder(record)
    for event in events:
        builder.feed(event)
    return builder.finish(tool_results=tool_results, approvals=approvals)


async def collect_history_facts(
    record: RunRecord,
    store: RuntimeStore,
    *,
    tool_results: tuple[ToolResultFact, ...] = (),
    approvals: tuple[StoredApproval, ...] = (),
) -> ResumeHistoryFacts:
    builder = _HistoryFactsBuilder(record)
    async for event in iter_run_events(
        store, record.state.identity.run_id, through_sequence=record.last_event_sequence
    ):
        builder.feed(event)
    return builder.finish(tool_results=tool_results, approvals=approvals)


def _chain_ended(
    chain: ExecutionChainFact, fresh: tuple[ExecutionEvidence, ...]
) -> bool:
    if not chain.identities:
        return False
    evidence = {item.execution_id: item for item in (*chain.evidence, *fresh)}
    for identity in chain.identities:
        fact = evidence.get(identity.execution_id)
        if (
            fact is None
            or fact.identity != identity
            or fact.outcome != "ended"
            or not fact.group_ended
            or not fact.issuer_ended
        ):
            return False
        if (
            isinstance(identity, DockerExecIdentity)
            and identity.start_permitted
            and not (identity.started_observed or fact.started_observed)
        ):
            return False
    return True


def _chain_ready(
    chain: ExecutionChainFact, environment: ResumeEnvironmentFacts
) -> bool:
    if _chain_ended(chain, environment.execution_evidence):
        return True
    if "guest_execution_groups" not in environment.attachment.live_checks:
        return False
    evidence = {
        item.execution_id: item
        for item in (*chain.evidence, *environment.execution_evidence)
    }
    for identity in chain.identities:
        fact = evidence.get(identity.execution_id)
        if fact is not None and fact.outcome == "active":
            return False
        if isinstance(identity, DockerExecIdentity):
            if not identity.started_observed or identity.guest is None:
                return False
            helper = next(
                (
                    item
                    for item in chain.identities
                    if item.execution_id == identity.helper_execution_id
                ),
                None,
            )
            if helper is None:
                return False
            helper_fact = evidence.get(helper.execution_id)
            if (
                helper_fact is None
                or helper_fact.identity != helper
                or helper_fact.outcome != "ended"
                or not helper_fact.group_ended
                or not helper_fact.issuer_ended
            ):
                return False
        elif (
            fact is None
            or fact.identity != identity
            or fact.outcome != "ended"
            or not fact.group_ended
            or not fact.issuer_ended
        ):
            return False
    return bool(chain.identities)


def plan_resume(
    record: RunRecord, history: ResumeHistoryFacts, environment: ResumeEnvironmentFacts
) -> ResumePlan | ResumeRejection:
    state = record.state
    context = record.recovery_context

    def reject(code: str, message: str) -> ResumeRejection:
        operation = state.execution.active_operation
        return ResumeRejection(
            code=code,
            run_id=state.identity.run_id,
            phase=state.execution.phase,
            source_sequence=record.last_event_sequence,
            step_number=operation.step_number if operation else None,
            call_index=operation.call_index if operation else None,
            tool_call_id=operation.tool_call_id if operation else None,
            workspace_id=context.workspace_id if context else None,
            message=message,
        )

    if state.lifecycle.status in TERMINAL_STATUSES:
        return reject(
            "RUN_NOT_RESUMABLE", "Terminal runs cannot resume business execution"
        )
    if context is None or context.attachment is None or context.application is None:
        return reject(
            "RESUME_METADATA_MISSING",
            "Retained attachment and frozen application definition are required",
        )
    try:
        RunState.model_validate(state.model_dump(mode="python"))
    except ValidationError as exc:
        return reject("RESUME_STATE_INCONSISTENT", str(exc))
    if history.history_errors:
        return reject("RESUME_STATE_INCONSISTENT", "; ".join(history.history_errors))
    if (
        environment.attachment.run_id != state.identity.run_id
        or environment.attachment.workspace_id != context.workspace_id
    ):
        return reject(
            "RESUME_STATE_INCONSISTENT", "Attachment facts belong to another workspace"
        )
    batch = state.execution.tool_batch
    step = state.execution.current_step
    if batch is not None and step is not None:
        prefix = [
            fact
            for fact in history.tool_results
            if fact.step_number == step.step_number
        ]
        if len(prefix) != batch.next_index or any(
            fact.call_index != index or fact.tool_call_id != batch.calls[index].id
            for index, fact in enumerate(prefix)
        ):
            return reject(
                "RESUME_STATE_INCONSISTENT",
                "Completed tool prefix differs from committed results",
            )
    operation = state.execution.active_operation
    if operation is not None and operation.kind == "tool":
        assert batch is not None and operation.call_index is not None
        call = batch.calls[operation.call_index]
        metadata = next(
            (
                item
                for item in state.definition.tool_contract_metadata
                if item.get("name") == call.name
            ),
            {},
        )
        if metadata.get("unknown_outcome", "reject") != "retry_read_only":
            return reject(
                "UNCERTAIN_TOOL_EXECUTION",
                f"{call.name} started at sequence {operation.started_sequence}; original workspace is retained; automatic replay is refused",
            )
    attachment = environment.attachment
    if attachment.errors:
        return reject(attachment.errors[0].code, attachment.errors[0].message)
    if not attachment.exists:
        return reject("WORKSPACE_MISSING", "Original workspace is missing")
    if attachment.identity_matches is not True:
        return reject(
            "WORKSPACE_IDENTITY_MISMATCH", "Original workspace identity is unconfirmed"
        )
    close = []
    for chain in history.execution_chains:
        if chain.attempt_id in history.interrupted_attempt_ids:
            continue
        if not _chain_ready(chain, environment):
            return reject(
                "WORKSPACE_BUSY",
                "Original execution chain has no verified whole-group end",
            )
    if operation is not None:
        if operation.attempt_id in history.interrupted_attempt_ids:
            return reject(
                "RESUME_STATE_INCONSISTENT", "Interrupted operation remains active"
            )
        if operation.kind not in ("model", "summary"):
            chain = next(
                (
                    chain
                    for chain in history.execution_chains
                    if chain.attempt_id == operation.attempt_id
                ),
                None,
            )
            if chain is None or not _chain_ready(chain, environment):
                return reject("WORKSPACE_BUSY", "Original operation end is unconfirmed")
        close.append(operation.attempt_id)
    rejections = (
        *environment.dependency_rejections,
        environment.candidate_rejection,
        environment.evaluation_rejection,
    )
    for rejection in rejections:
        if rejection is not None:
            return rejection
    status = state.lifecycle.status
    if status is RunStatus.SUSPENDED:
        assert state.recovery.suspension is not None
        status = RunStatus(state.recovery.suspension.business_status)
    elif status is RunStatus.RECOVERING:
        assert state.recovery.session is not None
        status = RunStatus(state.recovery.session.business_status)
    target: Literal["preparing", "running", "evaluating"] = (
        "preparing"
        if status in (RunStatus.CREATED, RunStatus.PREPARING)
        else "evaluating"
        if status is RunStatus.EVALUATING
        else "running"
    )
    invalidate = tuple(
        item.approval.request.approval_id
        for item in history.approvals
        if item.authorization_invalidated_event_id is None
        and (
            item.approval.resolution is None
            or item.approval.resolution.outcome is ApprovalOutcome.APPROVED
        )
    )
    cleanup = (
        (state.execution.evaluation.attempt,)
        if state.execution.evaluation.attempt is not None
        and state.execution.evaluation.result is None
        else ()
    )
    assert state.execution.phase is not None
    return ResumePlan(
        run_id=state.identity.run_id,
        source_sequence=record.last_event_sequence,
        target_status=target,
        phase=state.execution.phase,
        close_operations=tuple(close),
        invalidate_approval_ids=invalidate,
        live_checks=attachment.live_checks,
        cleanup_requirements=cleanup,
        readiness="requires_live_checks" if attachment.live_checks else "ready",
        diagnostics=(
            f"Status: {state.lifecycle.status.value}",
            f"Step: {state.execution.steps_started}; completed calls in current batch: {batch.next_index if batch else 0}",
            f"Workspace: {context.workspace_id}",
            f"Resume attempts (audit): {state.recovery.resume_attempts}",
            f"Confirmed cost: ${state.usage.cost_usd:.8f} USD",
            f"Unconfirmed model requests (including active request): {state.usage.unconfirmed_model_requests + int(operation is not None and operation.kind in ('model', 'summary'))}; additional charges may apply",
        ),
    )


class ResumeRejected(RuntimeError):
    def __init__(self, rejection: ResumeRejection) -> None:
        self.rejection = rejection
        self.code = rejection.code
        super().__init__(f"{rejection.code}: {rejection.message}")
