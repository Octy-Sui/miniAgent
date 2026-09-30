from collections.abc import Awaitable
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.policy import (
    ApprovalRecord,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
    PolicyEffect,
)
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalRequested,
    ApprovalResolved,
    EventPayload,
    NewRunEvent,
    RunEvent,
)
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.snapshots import RunSnapshot
from miniagent.runtime.state import RunState


class RunNotFoundError(LookupError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"Run not found: {run_id}")


class RunAlreadyExistsError(RuntimeError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"Run already exists: {run_id}")


class ApprovalConflictError(RuntimeError):
    pass


class ApprovalExpiredError(RuntimeError):
    def __init__(self, approval_id: UUID) -> None:
        self.approval_id = approval_id
        super().__init__(f"Approval has expired: {approval_id}")


class UnsupportedStorageVersionError(RuntimeError):
    pass


class CorruptStoredDataError(RuntimeError):
    pass


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: RunState
    last_event_sequence: int = Field(ge=0)
    latest_snapshot_version: int = Field(ge=0)
    recovery_context: RunRecoveryContext | None = None


class StoredApproval(BaseModel):
    model_config = ConfigDict(frozen=True)

    approval: ApprovalRecord
    version: int = Field(ge=1)
    request_event_id: UUID
    resolution_event_id: UUID | None = None
    authorization_invalidated_event_id: UUID | None = None


class CreateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["create"] = "create"
    request: ApprovalRequest
    decision: PolicyDecision
    request_event_id: UUID


class ResolveApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["resolve"] = "resolve"
    resolution: ApprovalResolution
    expected_version: int = Field(ge=1)
    resolution_event_id: UUID


class InvalidateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["invalidate"] = "invalidate"
    approval_id: UUID
    expected_version: int = Field(ge=1)
    invalidated_event_id: UUID


type ApprovalChange = Annotated[
    CreateApproval | ResolveApproval | InvalidateApproval,
    Field(discriminator="kind"),
]


class SnapshotChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: UUID
    expected_version: int = Field(ge=0)


class RunCommit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: RunState
    events: list[NewRunEvent] = Field(min_length=1)
    expected_sequence: int = Field(ge=0)
    recovery_context: RunRecoveryContext | None = None
    approval_changes: list[ApprovalChange] = Field(default_factory=list)
    snapshot: SnapshotChange | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "RunCommit":
        run_id = self.state.identity.run_id
        if any(event.run_id != run_id for event in self.events):
            raise ValueError("Commit events must belong to the current run")
        if (
            self.recovery_context is not None
            and self.recovery_context.workspace_spec.run_id != run_id
        ):
            raise ValueError("Commit recovery context must belong to the current run")

        resolving = 0
        for change in self.approval_changes:
            if isinstance(change, CreateApproval):
                if (
                    change.request.run_id != run_id
                    or change.decision.request.run_id != run_id
                ):
                    raise ValueError("Approval creation must belong to the current run")
                self._require_approval_event(
                    change.request_event_id,
                    ApprovalRequested,
                    change.request.approval_id,
                )
                ApprovalRecord(request=change.request, decision=change.decision)
                if change.decision.effect is not PolicyEffect.APPROVAL:
                    raise ValueError("Approval creation requires an approval decision")
                payload = self._event_payload(change.request_event_id)
                assert isinstance(payload, ApprovalRequested)
                policy_request = change.decision.request
                if (
                    payload.decision_id != change.decision.decision_id
                    or payload.call_index != policy_request.call_index
                    or payload.tool_call_id != policy_request.tool_call_id
                    or payload.tool_name != policy_request.tool_name
                ):
                    raise ValueError("Approval event scope must match its decision")
            elif isinstance(change, ResolveApproval):
                resolving += 1
                self._require_approval_event(
                    change.resolution_event_id,
                    ApprovalResolved,
                    change.resolution.approval_id,
                )
            else:
                self._require_approval_event(
                    change.invalidated_event_id,
                    ApprovalInvalidated,
                    change.approval_id,
                )
        if resolving > 1:
            raise ValueError("A commit may resolve at most one approval")
        if resolving and len(self.approval_changes) != 1:
            raise ValueError("An approval resolution must be the only approval change")
        return self

    def _event_payload(self, event_id: UUID) -> EventPayload:
        return next(
            event.payload for event in self.events if event.event_id == event_id
        )

    def _require_approval_event(
        self,
        event_id: UUID,
        payload_type: type[ApprovalRequested | ApprovalResolved | ApprovalInvalidated],
        approval_id: UUID,
    ) -> None:
        matches = [event for event in self.events if event.event_id == event_id]
        if len(matches) != 1:
            raise ValueError("Approval event ID must identify one commit event")
        payload = matches[0].payload
        if not isinstance(payload, payload_type) or payload.approval_id != approval_id:
            raise ValueError("Approval event must reference the changed approval")


class CommitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: RunRecord
    events: list[RunEvent]
    approvals: list[StoredApproval] = Field(default_factory=list)
    snapshot: RunSnapshot | None = None
    idempotent: bool = False


class RuntimeStore(Protocol):
    def create(
        self,
        state: RunState,
        *,
        recovery_context: RunRecoveryContext | None = None,
    ) -> Awaitable[RunRecord]: ...

    async def get(self, run_id: UUID) -> RunRecord: ...

    def commit(self, change: RunCommit) -> Awaitable[CommitResult]: ...

    async def load_version(self, run_id: UUID, version: int) -> RunSnapshot | None: ...

    async def list_events(
        self, run_id: UUID, *, after_sequence: int = 0, limit: int | None = None
    ) -> list[RunEvent]: ...
