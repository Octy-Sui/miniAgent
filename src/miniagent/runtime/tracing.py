from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    computed_field,
    model_validator,
)

from miniagent.artifacts.contracts import ArtifactRef


def _validate_span_id(value: str) -> str:
    if int(value, 16) == 0:
        raise ValueError("Span ID cannot be all zeroes")
    return value


SpanId = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{16}$"),
    AfterValidator(_validate_span_id),
]


class SpanCategory(StrEnum):
    RUN = "run"
    WORKSPACE = "workspace"
    STEP = "step"
    MODEL = "model"
    TOOL = "tool"
    TOOL_EXECUTION = "tool_execution"
    APPROVAL_WAIT = "approval_wait"
    CHECKPOINT = "checkpoint"
    RECOVERY = "recovery"
    EVALUATION = "evaluation"


class TraceSpanStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class TraceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    span_id: SpanId
    parent_span_id: SpanId | None = None
    category: SpanCategory
    name: str = Field(min_length=1, max_length=128)
    status: TraceSpanStatus
    start_sequence: int = Field(ge=1)
    end_sequence: int | None = Field(default=None, ge=1)
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    step_number: int | None = Field(default=None, ge=1)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_span(self) -> "TraceSpan":
        if self.parent_span_id == self.span_id:
            raise ValueError("Trace span cannot be its own parent")
        if self.category is SpanCategory.STEP and self.step_number is None:
            raise ValueError("Step span requires a step number")
        if self.category is not SpanCategory.STEP and self.step_number is not None:
            raise ValueError("Only a step span may carry a step number")
        if self.end_sequence is not None and self.end_sequence < self.start_sequence:
            raise ValueError("Trace span cannot end before it starts")
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("Trace span cannot end before it starts")

        if (self.end_sequence is None) != (self.ended_at is None):
            raise ValueError("Trace span end sequence and time must be recorded together")
        has_end = self.end_sequence is not None
        if self.duration_ms is not None and not has_end:
            raise ValueError("Trace span duration requires an end")
        if self.status is TraceSpanStatus.IN_PROGRESS and has_end:
            raise ValueError("In-progress trace span cannot have an end")
        if self.status in {
            TraceSpanStatus.OK,
            TraceSpanStatus.ERROR,
            TraceSpanStatus.CANCELLED,
        } and (self.end_sequence is None or self.ended_at is None):
            raise ValueError("Completed trace span requires an end")
        return self


_ALLOWED_PARENTS: dict[SpanCategory, frozenset[SpanCategory]] = {
    SpanCategory.RUN: frozenset(),
    SpanCategory.WORKSPACE: frozenset({SpanCategory.RUN}),
    SpanCategory.STEP: frozenset({SpanCategory.RUN}),
    SpanCategory.MODEL: frozenset({SpanCategory.STEP}),
    SpanCategory.TOOL: frozenset({SpanCategory.STEP}),
    SpanCategory.TOOL_EXECUTION: frozenset({SpanCategory.TOOL}),
    SpanCategory.APPROVAL_WAIT: frozenset({SpanCategory.TOOL}),
    SpanCategory.CHECKPOINT: frozenset(
        {SpanCategory.RUN, SpanCategory.RECOVERY, SpanCategory.TOOL}
    ),
    SpanCategory.RECOVERY: frozenset({SpanCategory.RUN}),
    SpanCategory.EVALUATION: frozenset({SpanCategory.RUN}),
}


class RunTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    spans: list[TraceSpan] = Field(min_length=1)

    @computed_field
    @property
    def trace_id(self) -> str:
        return self.run_id.hex

    @model_validator(mode="after")
    def validate_trace(self) -> "RunTrace":
        if self.run_id.int == 0:
            raise ValueError("Run Trace ID cannot be all zeroes")

        spans_by_id = {span.span_id: span for span in self.spans}
        if len(spans_by_id) != len(self.spans):
            raise ValueError("Run Trace span IDs must be unique")

        roots = [span for span in self.spans if span.parent_span_id is None]
        if len(roots) != 1 or roots[0].category is not SpanCategory.RUN:
            raise ValueError("Run Trace requires exactly one run root span")

        for span in self.spans:
            if span.category is SpanCategory.RUN:
                if span.parent_span_id is not None:
                    raise ValueError("Run span cannot have a parent")
                continue
            if span.parent_span_id is None:
                raise ValueError("Non-run trace span requires a parent")
            parent = spans_by_id.get(span.parent_span_id)
            if parent is None:
                raise ValueError("Trace span parent is missing")
            if parent.category not in _ALLOWED_PARENTS[span.category]:
                raise ValueError(
                    f"{span.category.value} span cannot be a child of "
                    f"{parent.category.value} span"
                )
        return self
