from dataclasses import dataclass, field
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactStore
from miniagent.cli.candidate_submission import CandidateSubmission, SubmissionResult
from miniagent.environments import Workspace
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    ExecutionIdentity,
    ExecutionObserver,
    HostProcessIdentity,
    ManagedExecutionError,
    MemoryExecutionObserver,
    current_cleanup_deadline,
)
from miniagent.environments.process import inspect_host_execution
from miniagent.evaluation import EvaluationError
from miniagent.runtime.events import (
    CandidateSubmissionCommitted,
    CandidateSubmissionFailed,
    CandidateSubmissionStarted,
    DiagnosticFinalizationCompleted,
    DiagnosticFinalizationExecutionEnded,
    DiagnosticFinalizationExecutionIdentified,
    DiagnosticFinalizationInterrupted,
    DiagnosticFinalizationStarted,
    EventPayload,
    NewRunEvent,
)
from miniagent.runtime.interruption import (
    InterruptionRequested,
    current_interruption_token,
)
from miniagent.runtime.progress import ComponentEnvelope, EvaluationProgress
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.resume import history_facts


@dataclass(slots=True)
class _DiagnosticObserver:
    recorder: RunRecorder
    deadline: float
    memory: MemoryExecutionObserver | None = None
    progress: EvaluationProgress = field(default_factory=EvaluationProgress)
    interrupted_at_start: bool = field(init=False)

    def __post_init__(self) -> None:
        token = current_interruption_token()
        self.interrupted_at_start = token is not None and token.is_requested

    def check_permission(self) -> None:
        token = current_interruption_token()
        if token is not None and token.is_requested and not self.interrupted_at_start:
            raise InterruptionRequested

    async def _append(self, payload: EventPayload) -> None:
        if monotonic() >= min(
            self.deadline, current_cleanup_deadline() or float("inf")
        ):
            raise ManagedExecutionError("Diagnostic commit deadline exhausted")
        record = self.recorder.record
        assert record is not None
        await self.recorder.commit(
            record.state,
            events=[NewRunEvent(run_id=self.recorder.run_id, payload=payload)],
        )

    def _require_ended(self) -> None:
        assert self.memory is not None
        for execution_id, identity in self.memory.identities.items():
            evidence = self.memory.evidence.get(execution_id)
            if (
                evidence is None
                or evidence.identity != identity
                or evidence.outcome != "ended"
                or not evidence.group_ended
                or not evidence.issuer_ended
            ):
                raise ManagedExecutionError(
                    "Diagnostic execution group end is unconfirmed"
                )

    async def record(
        self, payload: EventPayload, *, component: ComponentEnvelope | None = None
    ) -> None:
        if isinstance(payload, CandidateSubmissionStarted):
            await self._append(
                DiagnosticFinalizationStarted(
                    attempt_id=payload.attempt_id,
                    submission_id=payload.submission_id,
                    temporary_index_path=payload.temporary_index_path,
                )
            )
            self.memory = MemoryExecutionObserver(attempt_id=payload.attempt_id)
        elif isinstance(
            payload, (CandidateSubmissionCommitted, CandidateSubmissionFailed)
        ):
            if self.memory is None or self.memory.attempt_id != payload.attempt_id:
                raise ManagedExecutionError("Diagnostic result has no matching attempt")
            self._require_ended()
            await self._append(
                DiagnosticFinalizationCompleted(
                    attempt_id=payload.attempt_id,
                    submission_id=payload.submission_id,
                    status=payload.status
                    if isinstance(payload, CandidateSubmissionCommitted)
                    else "failed",
                    patch_ref=payload.patch_ref
                    if isinstance(payload, CandidateSubmissionCommitted)
                    else None,
                    patch_sha256=payload.patch_sha256
                    if isinstance(payload, CandidateSubmissionCommitted)
                    else None,
                    error=payload.error
                    if isinstance(payload, CandidateSubmissionFailed)
                    else None,
                )
            )
        else:
            raise ManagedExecutionError("Unsupported diagnostic observer fact")

    def execution_observer(self, attempt_id: UUID) -> ExecutionObserver:
        if self.memory is None or self.memory.attempt_id != attempt_id:
            raise ManagedExecutionError("Diagnostic execution has no committed attempt")
        return self

    @property
    def attempt_id(self) -> UUID:
        assert self.memory is not None
        return self.memory.attempt_id

    async def identified(
        self,
        identity: ExecutionIdentity,
        *,
        permission: Literal["observe", "create_exec", "start_exec"] = "observe",
    ) -> None:
        self.check_permission()
        assert self.memory is not None
        if identity.attempt_id != self.memory.attempt_id:
            raise ManagedExecutionError(
                "Diagnostic identity belongs to another attempt"
            )
        await self._append(
            DiagnosticFinalizationExecutionIdentified(
                attempt_id=self.attempt_id, identity=identity, permission=permission
            )
        )
        await self.memory.identified(identity, permission=permission)

    async def ended(self, evidence: ExecutionEvidence) -> None:
        assert self.memory is not None
        if self.memory.identities.get(evidence.execution_id) != evidence.identity:
            raise ManagedExecutionError("Diagnostic end fact has no matching identity")
        await self._append(
            DiagnosticFinalizationExecutionEnded(
                attempt_id=self.attempt_id, evidence=evidence
            )
        )
        await self.memory.ended(evidence)


async def finalize_diagnostic(
    *,
    recorder: RunRecorder,
    workspace: Workspace,
    artifact_store: ArtifactStore,
    deadline: float,
    cleanup_timeout_seconds: float = 5.0,
) -> SubmissionResult:
    record = recorder.record
    store = recorder.store
    if (
        record is None
        or store is None
        or record.state.identity.run_id != workspace.run_id
    ):
        raise ManagedExecutionError(
            "Diagnostic finalization requires the original persisted run"
        )
    observer = _DiagnosticObserver(recorder, deadline)
    operation = record.state.execution.active_operation
    if operation is not None and operation.kind not in ("model", "summary"):
        await observer._append(
            DiagnosticFinalizationInterrupted(
                attempt_id=uuid4(),
                reason="Diagnostic export skipped: business execution outcome is unconfirmed",
            )
        )
        return SubmissionResult(
            status="failed",
            error=EvaluationError(
                code="DIAGNOSTIC_EXECUTION_UNCONFIRMED",
                message="Diagnostic export skipped because prior execution is unconfirmed",
            ),
        )
    events = tuple(await store.list_events(recorder.run_id))
    history = history_facts(record, events)
    if history.history_errors:
        raise ManagedExecutionError("Diagnostic history is inconsistent")
    for chain in history.execution_chains:
        if chain.attempt_id in history.interrupted_attempt_ids:
            continue
        evidence = {item.execution_id: item for item in chain.evidence}
        for identity in chain.identities:
            if identity.execution_id not in evidence and isinstance(
                identity, HostProcessIdentity
            ):
                evidence[identity.execution_id] = inspect_host_execution(identity)
        if not chain.identities or any(
            (fact := evidence.get(identity.execution_id)) is None
            or fact.identity != identity
            or fact.outcome != "ended"
            or not fact.group_ended
            or not fact.issuer_ended
            for identity in chain.identities
        ):
            await observer._append(
                DiagnosticFinalizationInterrupted(
                    attempt_id=chain.attempt_id,
                    reason="Diagnostic export skipped: prior execution end is unconfirmed",
                )
            )
            return SubmissionResult(
                status="failed",
                error=EvaluationError(
                    code="DIAGNOSTIC_EXECUTION_UNCONFIRMED",
                    message="Diagnostic export skipped because prior execution is unconfirmed",
                ),
            )
        if chain.scope != "diagnostic":
            continue
        for identity in chain.identities:
            fact = evidence[identity.execution_id]
            if fact not in chain.evidence:
                await observer._append(
                    DiagnosticFinalizationExecutionEnded(
                        attempt_id=chain.attempt_id, evidence=fact
                    )
                )
        await observer._append(
            DiagnosticFinalizationInterrupted(
                attempt_id=chain.attempt_id,
                reason="Prior diagnostic attempt interrupted",
                execution_ended=True,
            )
        )
    remaining = deadline - monotonic()
    if remaining <= cleanup_timeout_seconds:
        return SubmissionResult(
            status="failed",
            error=EvaluationError(
                code="DIAGNOSTIC_DEADLINE_EXHAUSTED",
                message="No diagnostic export budget remains",
            ),
        )
    submission = CandidateSubmission(
        workspace,
        artifact_store,
        timeout_seconds=remaining,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
        diagnostic_observer_getter=lambda: observer,
    )
    return await submission.submit(kind="diagnostic")
