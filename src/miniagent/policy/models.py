# File   : models.py
# Created: 2026-09-17 17:09:16
# Author : octy Sui

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL = "approval"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class PolicyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: str = Field(min_length=1)
    workspace_kind: Literal["local", "docker"]
    revision: str = Field(min_length=1)


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    step_number: int = Field(ge=1)
    call_index: int = Field(ge=0)
    tool_call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, JsonValue]
    agent_spec_digest: str
    tool_contract_digest: str
    workspace_id: str = Field(min_length=1)
    workspace_kind: Literal["local", "docker"]
    execution_context_digest: str

    @model_validator(mode="after")
    def validate_digests(self) -> "PolicyRequest":
        for value in (
            self.agent_spec_digest,
            self.tool_contract_digest,
            self.execution_context_digest,
        ):
            if not _DIGEST_PATTERN.fullmatch(value):
                raise ValueError("Policy request digests must be lowercase SHA-256")
        return self


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    effect: PolicyEffect
    risk: RiskLevel
    matched_rule_ids: tuple[str, ...]
    decisive_rule_ids: tuple[str, ...]
    reason_code: str
    reason: str


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    decision_id: UUID
    request: PolicyRequest
    request_digest: str
    policy_revision: str = Field(min_length=1)
    effect: PolicyEffect
    risk: RiskLevel
    matched_rule_ids: tuple[str, ...]
    decisive_rule_ids: tuple[str, ...]
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    input_summary: str = Field(min_length=1, max_length=2048)
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def validate_rules_and_digest(self) -> "PolicyDecision":
        if not _DIGEST_PATTERN.fullmatch(self.request_digest):
            raise ValueError("Policy decision digest must be lowercase SHA-256")
        if len(self.matched_rule_ids) != len(set(self.matched_rule_ids)):
            raise ValueError("Matched policy rule IDs must be unique")
        if tuple(sorted(self.matched_rule_ids)) != self.matched_rule_ids:
            raise ValueError("Matched policy rule IDs must be sorted")
        if not self.decisive_rule_ids:
            raise ValueError("Policy decision requires a decisive rule")
        if len(self.decisive_rule_ids) != len(set(self.decisive_rule_ids)):
            raise ValueError("Decisive policy rule IDs must be unique")
        if tuple(sorted(self.decisive_rule_ids)) != self.decisive_rule_ids:
            raise ValueError("Decisive policy rule IDs must be sorted")
        if not set(self.decisive_rule_ids) <= set(self.matched_rule_ids):
            raise ValueError("Decisive rules must be matched rules")
        return self


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
