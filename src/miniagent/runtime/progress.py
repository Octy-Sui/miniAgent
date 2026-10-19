from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from miniagent.environments.managed_execution import ExecutionIdentity
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.result import RunError, TerminationReason


class _Progress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExecutionPhase(StrEnum):
    PREPARE = "prepare"
    REQUEST_MODEL = "request_model"
    EXECUTE_TOOLS = "execute_tools"
    COMPLETE_STEP = "complete_step"
    EVALUATE_CANDIDATE = "evaluate_candidate"
    TERMINATE = "terminate"


class CurrentStep(_Progress):
    step_number: int = Field(ge=1)
    message_start: int = Field(ge=0)
    started_event_id: UUID
    started_sequence: int = Field(ge=1)


class RequestProgress(_Progress):
    branch: Literal["boundary", "business", "forced_compaction"] = "boundary"
    business_retry_used: int = Field(default=0, ge=0, le=1)
    summary_retry_used: int = Field(default=0, ge=0)
    source_version: int | None = Field(default=None, ge=1)
    retry_pending: bool = False
    last_failure: RunError | None = None


class ComponentEnvelope(_Progress):
    kind: str = Field(min_length=1)
    schema_version: int = Field(ge=1)
    payload: dict[str, JsonValue]


class TerminationDecision(_Progress):
    status: Literal["succeeded", "failed", "cancelled"]
    reason: TerminationReason
    error: RunError | None = None
    final_output: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "TerminationDecision":
        if self.status == "succeeded" and not self.final_output:
            raise ValueError("Succeeded decision requires final_output")
        if self.status == "succeeded" and self.error is not None:
            raise ValueError("Succeeded decision cannot include error")
        return self


class ToolsDisposition(_Progress):
    kind: Literal["tools"] = "tools"


class CandidateDisposition(_Progress):
    kind: Literal["candidate"] = "candidate"
    text: str = Field(min_length=1)


class TerminateDisposition(_Progress):
    kind: Literal["terminate"] = "terminate"
    decision: TerminationDecision


ResponseDisposition = Annotated[
    ToolsDisposition | CandidateDisposition | TerminateDisposition,
    Field(discriminator="kind"),
]


class OperationRef(_Progress):
    schema_version: Literal[1] = 1
    kind: Literal["model", "summary", "tool", "patch_export", "evaluation"]
    attempt_id: UUID
    started_event_id: UUID
    started_sequence: int = Field(ge=1)
    step_number: int | None = Field(default=None, ge=1)
    call_index: int | None = Field(default=None, ge=0)
    tool_call_id: str | None = Field(default=None, min_length=1)
    executions: tuple[ExecutionIdentity, ...] = ()
    component: ComponentEnvelope | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "OperationRef":
        if self.kind in ("model", "tool") and self.step_number is None:
            raise ValueError("model/tool operation requires step_number")
        if self.kind == "tool":
            if self.call_index is None:
                raise ValueError("tool operation requires call_index")
            if self.tool_call_id is None:
                raise ValueError("tool operation requires tool_call_id")
        elif self.call_index is not None or self.tool_call_id is not None:
            raise ValueError("Only tool operations may have call_index/tool_call_id")
        ids = [identity.execution_id for identity in self.executions]
        if len(ids) != len(set(ids)):
            raise ValueError("execution_id must be unique within an operation")
        if any(identity.attempt_id != self.attempt_id for identity in self.executions):
            raise ValueError("execution attempt_id must match operation attempt_id")
        return self


BusinessStatus = Literal[
    "created", "preparing", "running", "waiting_approval", "evaluating"
]


class RecoverySession(_Progress):
    attempt_id: UUID
    source_sequence: int = Field(ge=0)
    business_status: BusinessStatus
    phase_at_start: ExecutionPhase


class InterruptionInfo(_Progress):
    signal_number: Literal[2, 15]
    requested_at: datetime


class SuspensionMetadata(_Progress):
    interruption: InterruptionInfo
    business_status: BusinessStatus
    suspended_at: datetime


class EvaluationProgress(_Progress):
    submission: ComponentEnvelope | None = None
    attempt: ComponentEnvelope | None = None
    result: EvaluationResult | None = None
