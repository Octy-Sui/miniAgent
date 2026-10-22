from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from statistics import fmean
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from miniagent.models.protocol import ModelRequestTiming
from miniagent.runtime.events import (
    CompactionAttemptRecorded,
    CompactionRequested,
    ModelRequested,
    ModelResponded,
    OperationInterrupted,
    RunEvent,
    ToolEnded,
    ToolExecutionEnded,
    ToolExecutionStarted,
)


class DurationStatistics(BaseModel):
    samples: int = 0
    total: float | None = None
    mean: float | None = None
    p50: float | None = None
    p95: float | None = None
    maximum: float | None = None


class CallMeasurement(BaseModel):
    attempt_id: UUID
    kind: Literal["business", "compaction", "tool"]
    name: str | None = None
    outcome: Literal["success", "failure", "interrupted", "unknown"] = "unknown"
    error_code: str | None = None
    duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    server_duration_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    event_elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provider_request_id: str | None = None


class CallStatistics(BaseModel):
    started: int
    succeeded: int
    failed: int
    interrupted: int
    unknown: int
    success_rate: float | None
    failure_rate: float | None
    interrupted_rate: float | None
    unknown_rate: float | None
    duration_ms: DurationStatistics
    server_duration_ms: DurationStatistics
    event_elapsed_ms: DurationStatistics


def _durations(values: list[float]) -> DurationStatistics:
    if not values:
        return DurationStatistics()
    ordered = sorted(values)
    # Nearest-rank percentiles are calculated from calls, never task percentiles.
    return DurationStatistics(
        samples=len(values),
        total=sum(values),
        mean=fmean(values),
        p50=ordered[ceil(0.5 * len(values)) - 1],
        p95=ordered[ceil(0.95 * len(values)) - 1],
        maximum=ordered[-1],
    )


def summarize_calls(calls: list[CallMeasurement]) -> CallStatistics:
    counts = Counter(call.outcome for call in calls)
    return CallStatistics(
        started=len(calls),
        succeeded=counts["success"],
        failed=counts["failure"],
        interrupted=counts["interrupted"],
        unknown=counts["unknown"],
        success_rate=counts["success"] / len(calls) if calls else None,
        failure_rate=counts["failure"] / len(calls) if calls else None,
        interrupted_rate=counts["interrupted"] / len(calls) if calls else None,
        unknown_rate=counts["unknown"] / len(calls) if calls else None,
        duration_ms=_durations(
            [c.duration_ms for c in calls if c.duration_ms is not None]
        ),
        server_duration_ms=_durations(
            [c.server_duration_ms for c in calls if c.server_duration_ms is not None]
        ),
        event_elapsed_ms=_durations(
            [c.event_elapsed_ms for c in calls if c.event_elapsed_ms is not None]
        ),
    )


class ModelStatistics(BaseModel):
    overall: CallStatistics
    business: CallStatistics
    compaction: CallStatistics
    calls: list[CallMeasurement]


class ToolStatistics(BaseModel):
    overall: CallStatistics
    by_tool: dict[str, CallStatistics]
    not_executed: dict[str, int]
    calls: list[CallMeasurement]


def summarize_models(calls: list[CallMeasurement]) -> ModelStatistics:
    return ModelStatistics(
        overall=summarize_calls(calls),
        business=summarize_calls([c for c in calls if c.kind == "business"]),
        compaction=summarize_calls([c for c in calls if c.kind == "compaction"]),
        calls=calls,
    )


def summarize_tools(
    calls: list[CallMeasurement], not_executed: dict[str, int]
) -> ToolStatistics:
    return ToolStatistics(
        overall=summarize_calls(calls),
        by_tool={
            name: summarize_calls([c for c in calls if c.name == name])
            for name in sorted({c.name for c in calls if c.name is not None})
        },
        not_executed=not_executed,
        calls=calls,
    )


@dataclass
class _StartedCall:
    measurement: CallMeasurement
    occurred_at: datetime
    step_number: int | None
    span_id: str | None
    tool_scope: tuple[int, str, str] | None
    closed: bool = False


class MetricsCollector:
    def __init__(self) -> None:
        self.calls: dict[UUID, _StartedCall] = {}
        self.not_executed: Counter[str] = Counter()

    def observe(self, event: RunEvent) -> None:
        payload = event.payload
        if isinstance(
            payload, (ModelRequested, CompactionRequested, ToolExecutionStarted)
        ):
            kind = (
                "business"
                if isinstance(payload, ModelRequested)
                else "compaction"
                if isinstance(payload, CompactionRequested)
                else "tool"
            )
            name = (
                payload.tool_name
                if isinstance(payload, ToolExecutionStarted)
                else payload.model
            )
            identity = payload.attempt_id or event.event_id
            self.calls[identity] = _StartedCall(
                CallMeasurement(attempt_id=identity, kind=kind, name=name),
                event.occurred_at,
                event.step_number,
                event.span_id,
                (payload.call_index, payload.tool_call_id, payload.tool_name)
                if isinstance(payload, ToolExecutionStarted)
                else None,
            )
            return
        if not isinstance(
            payload,
            (
                ModelResponded,
                CompactionAttemptRecorded,
                ToolExecutionEnded,
                ToolEnded,
                OperationInterrupted,
            ),
        ):
            return
        started = (
            self.calls.get(payload.attempt_id)
            if payload.attempt_id is not None
            else None
        )
        if started is None and payload.attempt_id is None:
            candidates = [
                candidate
                for candidate in self.calls.values()
                if candidate.step_number == event.step_number
                and (
                    isinstance(payload, ModelResponded)
                    and candidate.measurement.kind == "business"
                    and not candidate.closed
                    and candidate.span_id == event.span_id
                    or isinstance(payload, ToolEnded)
                    and candidate.tool_scope
                    == (payload.call_index, payload.tool_call_id, payload.tool_name)
                )
            ]
            if len(candidates) == 1:
                started = candidates[0]
        if started is None:
            if isinstance(payload, ToolEnded) and payload.return_validation.reason in (
                "tool_not_executed",
                "run_terminated",
            ):
                self.not_executed[payload.error_code or payload.status] += 1
            return
        call = started.measurement
        elapsed = (event.occurred_at - started.occurred_at).total_seconds() * 1000
        timing: ModelRequestTiming | None = None
        if isinstance(payload, ModelResponded):
            success = (
                payload.response is not None
                and payload.normalization_failure is None
                and payload.error_code is None
                and payload.response.error is None
                and payload.finish_reason not in ("error", "unknown", "content_filter")
            )
            call.outcome = "success" if success else "failure"
            call.error_code = payload.error_code
            timing = payload.request_timing or (
                payload.response.request_timing if payload.response else None
            )
            if (
                timing is None
                and payload.response
                and payload.response.latency_ms is not None
                and payload.response.latency_ms >= 0
            ):
                call.duration_ms = payload.response.latency_ms
        elif isinstance(payload, CompactionAttemptRecorded):
            call.outcome = (
                "success"
                if payload.response is not None
                and payload.normalization_failure is None
                and payload.result.error_code is None
                and payload.response.error is None
                and payload.response.finish_reason.value
                not in ("error", "unknown", "content_filter")
                else "interrupted"
                if payload.result.error_code == "CANCELLED"
                else "failure"
            )
            call.error_code = payload.result.error_code
            timing = payload.result.request_timing or (
                payload.response.request_timing if payload.response else None
            )
        elif isinstance(payload, OperationInterrupted):
            call.outcome = "failure" if payload.error_code else "interrupted"
            call.error_code = payload.error_code
            timing = payload.request_timing
        elif isinstance(payload, ToolExecutionEnded):
            call.outcome = (
                "success"
                if payload.status == "success"
                else "interrupted"
                if payload.status in ("cancelled", "suspended")
                else "failure"
            )
            call.error_code = payload.error_code
        elif isinstance(payload, ToolEnded):
            if payload.result is not None:
                call.duration_ms = payload.result.duration_ms
            # TOOL_END is outside the execution interval; keep the execution end.
            return
        started.closed = True
        if elapsed >= 0:
            call.event_elapsed_ms = elapsed
        if timing is not None:
            call.duration_ms = timing.client_duration_ms
            call.server_duration_ms = timing.server_duration_ms
            call.provider_request_id = timing.provider_request_id

    def models(self) -> ModelStatistics:
        return summarize_models(
            [c.measurement for c in self.calls.values() if c.measurement.kind != "tool"]
        )

    def tools(self) -> ToolStatistics:
        return summarize_tools(
            [
                c.measurement
                for c in self.calls.values()
                if c.measurement.kind == "tool"
            ],
            dict(self.not_executed),
        )
