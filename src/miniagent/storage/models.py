from collections.abc import Awaitable
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.policy import (
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
    PolicyEffect,
)
from miniagent.runtime.events import NewRunEvent, RunEvent
from miniagent.runtime.persistence import RunRecoveryContext
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


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: ApprovalRequest
    decision: PolicyDecision
    resolution: ApprovalResolution | None = None
    version: int = Field(ge=1)
    request_event_sequence: int = Field(ge=1)
    resolution_event_sequence: int | None = Field(default=None, ge=1)
    authorization_invalidated_event_sequence: int | None = Field(
        default=None,
        ge=1,
    )

    @model_validator(mode="after")
    def validate_links(self) -> "ApprovalRecord":
        if self.request.decision_id != self.decision.decision_id:
            raise ValueError("Approval request must reference its policy decision")
        if self.request.run_id != self.decision.request.run_id:
            raise ValueError(
                "Approval request and decision must belong to the same run"
            )
        if self.request.request_digest != self.decision.request_digest:
            raise ValueError("Approval request and decision digests must match")
        if self.request.policy_revision != self.decision.policy_revision:
            raise ValueError("Approval request and decision revisions must match")
        if (
            self.resolution is not None
            and self.resolution.approval_id != self.request.approval_id
        ):
            raise ValueError("Approval resolution must reference its request")
        return self


class CreateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["create"] = "create"
    request: ApprovalRequest
    decision: PolicyDecision
    request_event_index: int = Field(ge=0)


class ResolveApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["resolve"] = "resolve"
    resolution: ApprovalResolution
    expected_version: int = Field(ge=1)
    resolution_event_index: int = Field(ge=0)


class InvalidateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["invalidate"] = "invalidate"
    approval_id: UUID
    expected_version: int = Field(ge=1)
    invalidated_event_index: int = Field(ge=0)


ApprovalChange = Annotated[
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
        run_id = self.state.run_id
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
                if change.request_event_index >= len(self.events):
                    raise ValueError("Approval request event index is out of range")
                if change.request.decision_id != change.decision.decision_id:
                    raise ValueError(
                        "Approval request must reference its policy decision"
                    )
                if change.request.request_digest != change.decision.request_digest:
                    raise ValueError("Approval request and decision digests must match")
                if change.request.policy_revision != change.decision.policy_revision:
                    raise ValueError(
                        "Approval request and decision revisions must match"
                    )
                if change.decision.effect is not PolicyEffect.APPROVAL:
                    raise ValueError("Approval creation requires an approval decision")
            elif isinstance(change, ResolveApproval):
                resolving += 1
                if change.resolution_event_index >= len(self.events):
                    raise ValueError("Approval resolution event index is out of range")
            elif change.invalidated_event_index >= len(self.events):
                raise ValueError("Approval invalidation event index is out of range")
        if resolving > 1:
            raise ValueError("A commit may resolve at most one approval")
        if resolving and len(self.approval_changes) != 1:
            raise ValueError("An approval resolution must be the only approval change")
        return self


class CommitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    record: RunRecord
    events: list[RunEvent]
    approvals: list[ApprovalRecord] = Field(default_factory=list)
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
