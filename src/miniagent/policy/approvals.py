# File   : approvals.py
# Created: 2026-09-17 17:09:38
# Author : octy Sui

import re
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from miniagent.policy.contracts import PolicyDecision

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    approval_id: UUID
    decision_id: UUID
    run_id: UUID
    request_digest: str
    policy_revision: str = Field(min_length=1)
    requested_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_request(self) -> "ApprovalRequest":
        if not _DIGEST_PATTERN.fullmatch(self.request_digest):
            raise ValueError("Approval request digest must be lowercase SHA-256")
        if self.expires_at <= self.requested_at:
            raise ValueError("Approval request must expire after it is requested")
        return self


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ApprovalResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    approval_id: UUID
    outcome: ApprovalOutcome
    actor_kind: Literal["human", "system"]
    actor_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    resolved_at: AwareDatetime

    @model_validator(mode="after")
    def validate_actor(self) -> "ApprovalResolution":
        human_outcomes = {ApprovalOutcome.APPROVED, ApprovalOutcome.REJECTED}
        if (self.outcome in human_outcomes) != (self.actor_kind == "human"):
            raise ValueError("Approval resolution actor does not match its outcome")
        return self


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: ApprovalRequest
    decision: PolicyDecision
    resolution: ApprovalResolution | None = None

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


class ApprovalHandler(Protocol):
    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution: ...
