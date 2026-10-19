from collections.abc import Awaitable
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.metering.usage import RequestCost
from miniagent.models.protocol import ModelUsage
from miniagent.policy import PolicyDecision
from miniagent.runtime.tracing import SpanId
from miniagent.tools.contracts import ToolResult


class EventSequenceConflictError(RuntimeError):
    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected event sequence {expected}, found {actual}")


class RunEventType(StrEnum):
    COMPACTION_REQUESTED = "compaction_requested"
    COMPACTION_ATTEMPT_RECORDED = "compaction_attempt_recorded"
    COMPACTION_FAILED = "compaction_failed"
    COMPACTION_COMPLETED = "compaction_completed"
    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONDED = "model_responded"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_EXECUTION_START = "tool_execution_start"
    TOOL_EXECUTION_END = "tool_execution_end"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    APPROVAL_INVALIDATED = "approval_invalidated"
    EVALUATION_START = "evaluation_start"
    EVALUATION_COMPLETED = "evaluation_completed"
    RUN_FINISHED = "run_finished"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunStarted(_Payload):
    type: Literal[RunEventType.RUN_STARTED] = RunEventType.RUN_STARTED


class StepStarted(_Payload):
    type: Literal[RunEventType.STEP_STARTED] = RunEventType.STEP_STARTED


class StepCompleted(_Payload):
    type: Literal[RunEventType.STEP_COMPLETED] = RunEventType.STEP_COMPLETED
    failed: bool
    consecutive_failed_steps: int | None = Field(default=None, ge=0)
    candidate_output: bool = False


class ModelRequested(_Payload):
    type: Literal[RunEventType.MODEL_REQUESTED] = RunEventType.MODEL_REQUESTED
    provider: str
    model: str
    attempt: int = Field(default=1, ge=1)


class ModelResponded(_Payload):
    type: Literal[RunEventType.MODEL_RESPONDED] = RunEventType.MODEL_RESPONDED
    finish_reason: str
    tool_call_count: int = Field(ge=0)
    error_code: str | None = None


class ToolScopePayload(_Payload):
    call_index: int = Field(ge=0)
    tool_call_id: str
    tool_name: str


class ToolStarted(ToolScopePayload):
    type: Literal[RunEventType.TOOL_START] = RunEventType.TOOL_START


class ToolEnded(ToolScopePayload):
    type: Literal[RunEventType.TOOL_END] = RunEventType.TOOL_END
    status: str
    error_code: str | None = None
    reason: str | None = None
    result: ToolResult | None = None

    @model_validator(mode="after")
    def validate_result(self) -> "ToolEnded":
        if self.result is None:
            return self
        if self.result.tool_call_id != self.tool_call_id:
            raise ValueError("Tool end result must match its tool call")
        if self.result.status.value != self.status:
            raise ValueError("Tool end status must match its result")
        result_error_code = (
            self.result.error.code if self.result.error is not None else None
        )
        if result_error_code != self.error_code:
            raise ValueError("Tool end error code must match its result")
        return self


class ToolExecutionStarted(ToolScopePayload):
    type: Literal[RunEventType.TOOL_EXECUTION_START] = RunEventType.TOOL_EXECUTION_START


class ToolExecutionEnded(ToolScopePayload):
    type: Literal[RunEventType.TOOL_EXECUTION_END] = RunEventType.TOOL_EXECUTION_END
    status: str
    error_code: str | None = None
    reason: str | None = None


class PolicyDecided(ToolScopePayload):
    type: Literal[RunEventType.POLICY_DECIDED] = RunEventType.POLICY_DECIDED
    decision: PolicyDecision


class ApprovalRequested(ToolScopePayload):
    type: Literal[RunEventType.APPROVAL_REQUESTED] = RunEventType.APPROVAL_REQUESTED
    approval_id: UUID
    decision_id: UUID


class ApprovalResolved(ToolScopePayload):
    type: Literal[RunEventType.APPROVAL_RESOLVED] = RunEventType.APPROVAL_RESOLVED
    approval_id: UUID
    outcome: str


class ApprovalInvalidated(_Payload):
    type: Literal[RunEventType.APPROVAL_INVALIDATED] = RunEventType.APPROVAL_INVALIDATED
    approval_id: UUID
    reason: str


class EvaluationStart(_Payload):
    type: Literal[RunEventType.EVALUATION_START] = RunEventType.EVALUATION_START


class EvaluationCompleted(_Payload):
    type: Literal[RunEventType.EVALUATION_COMPLETED] = RunEventType.EVALUATION_COMPLETED
    passed: bool


class RunFinished(_Payload):
    type: Literal[RunEventType.RUN_FINISHED] = RunEventType.RUN_FINISHED
    status: str
    reason: str


class CompactionAttempt(_Payload):
    attempt: int = Field(ge=1)
    usage: ModelUsage | None = None
    request_cost: RequestCost | None = None
    provider_request_id: str | None = None
    error_code: str | None = None


class CompactionRequested(_Payload):
    type: Literal[RunEventType.COMPACTION_REQUESTED] = RunEventType.COMPACTION_REQUESTED
    provider: str
    model: str
    attempt: int = Field(ge=1)
    source_snapshot_version: int | None
    covered_messages: tuple[int, int]


class CompactionAttemptRecorded(_Payload):
    type: Literal[RunEventType.COMPACTION_ATTEMPT_RECORDED] = (
        RunEventType.COMPACTION_ATTEMPT_RECORDED
    )
    result: CompactionAttempt


class CompactionFailed(_Payload):
    type: Literal[RunEventType.COMPACTION_FAILED] = RunEventType.COMPACTION_FAILED
    code: str
    reason: str
    attempts: tuple[CompactionAttempt, ...] = ()


class CompactionCompleted(_Payload):
    type: Literal[RunEventType.COMPACTION_COMPLETED] = RunEventType.COMPACTION_COMPLETED
    previous_version: int | None = Field(default=None, ge=1)
    current_version: int = Field(ge=1)
    source_snapshot_version: int | None = Field(default=None, ge=1)
    source_snapshot_id: UUID | None = None
    source_event_sequence: int = Field(ge=0)
    new_snapshot_version: int | None = Field(default=None, ge=1)
    new_snapshot_id: UUID | None = None
    event_sequence: int | None = Field(default=None, ge=1)
    covered_messages: tuple[int, int]
    covered_steps: tuple[int, ...]
    retained_first_step: int = Field(ge=1)
    template_version: Literal[1] = 1
    summary_sha256: str
    provider: str
    model: str
    context_window_tokens: int
    output_reserve: int
    safety_margin: int
    business_budget: int
    summary_reserve: int
    old_input_tokens: int
    new_input_tokens: int
    summary_input_tokens: int
    hard_trigger: bool
    attempts: tuple[CompactionAttempt, ...] = ()


type EventPayload = Annotated[
    CompactionRequested
    | CompactionAttemptRecorded
    | CompactionFailed
    | CompactionCompleted
    | RunStarted
    | StepStarted
    | StepCompleted
    | ModelRequested
    | ModelResponded
    | ToolStarted
    | ToolEnded
    | ToolExecutionStarted
    | ToolExecutionEnded
    | PolicyDecided
    | ApprovalRequested
    | ApprovalResolved
    | ApprovalInvalidated
    | EvaluationStart
    | EvaluationCompleted
    | RunFinished,
    Field(discriminator="type"),
]


class NewRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[3] = 3
    run_id: UUID
    event_id: UUID = Field(default_factory=uuid4)
    payload: EventPayload
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    step_number: int | None = Field(default=None, ge=1)
    span_id: SpanId | None = None
    parent_span_id: SpanId | None = None

    @property
    def type(self) -> RunEventType:
        return self.payload.type

    @model_validator(mode="after")
    def validate_trace_scope(self) -> "NewRunEvent":
        if self.parent_span_id is not None and self.span_id is None:
            raise ValueError("Parent span requires a current span")
        if self.parent_span_id == self.span_id and self.span_id is not None:
            raise ValueError("Event span cannot be its own parent")
        if isinstance(self.payload, (StepStarted, StepCompleted)):
            if self.step_number is None:
                raise ValueError("Step event requires a step number")
            if self.span_id is None:
                raise ValueError("Step event requires a span")
        if isinstance(self.payload, ToolScopePayload):
            if self.step_number is None:
                raise ValueError("Tool-scoped event requires a step number")
            if self.span_id is None:
                raise ValueError("Tool-scoped event requires a span")
        return self


class RunEvent(NewRunEvent):
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime


class EventStore(Protocol):
    def append(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> Awaitable[RunEvent]: ...

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[RunEvent]: ...

    async def latest_sequence(self, run_id: UUID) -> int: ...
