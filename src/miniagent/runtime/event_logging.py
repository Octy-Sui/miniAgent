"""Allowlisted projections of committed journal facts for operational logs."""

import logging

from miniagent.diagnostics import log_event
from miniagent.runtime.events import (
    OperationOutputRecorded,
    RunEvent,
    RunFinished,
    ToolEnded,
)
from miniagent.runtime.state import RunState

_FIELDS = (
    "attempt_id",
    "provider",
    "model",
    "attempt",
    "finish_reason",
    "tool_call_count",
    "error_code",
    "tool_name",
    "tool_call_id",
    "status",
    "failed",
    "passed",
    "old_input_tokens",
    "new_input_tokens",
    "cost_status",
    "phase",
    "instance_id",
    "approval_id",
    "outcome",
)
_DEBUG_EVENTS = {
    "operation_output_recorded",
    "operation_execution_identified",
    "operation_execution_ended",
    "policy_decided",
}


def log_committed_event(event: RunEvent, state: RunState) -> None:
    payload = event.payload
    fields = {name: getattr(payload, name, None) for name in _FIELDS}
    level = logging.DEBUG if event.type.value in _DEBUG_EVENTS else logging.INFO
    if fields["error_code"] or event.type.value.endswith("failed"):
        level = logging.WARNING
    decision = getattr(payload, "decision", None)
    if decision is not None:
        fields["status"] = getattr(decision, "status", None)
        effect = getattr(decision, "effect", None)
        if effect is not None:
            fields["status"] = str(effect)
            if str(effect) != "allow":
                level = logging.WARNING
        error = getattr(decision, "error", None)
        if error is not None:
            fields["error_code"] = error.code
    response = getattr(payload, "response", None)
    if response is not None and response.usage is not None:
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            fields[name] = getattr(response.usage, name)
    timing = getattr(payload, "request_timing", None)
    if timing is not None:
        fields["duration_ms"] = timing.client_duration_ms
    result = getattr(payload, "result", None)
    if result is not None and getattr(result, "error", None) is not None:
        fields["error_code"] = result.error.code
    if isinstance(payload, ToolEnded) and payload.result is not None:
        fields["duration_ms"] = payload.result.duration_ms
        data = payload.result.data or {}
        if type(data.get("exit_code")) is int:
            fields["exit_code"] = data["exit_code"]
    if isinstance(payload, OperationOutputRecorded):
        fields["attempt_id"] = payload.output.attempt_id
        fields["stdout_bytes"] = payload.output.stdout.received_bytes
        fields["stderr_bytes"] = payload.output.stderr.received_bytes
    if event.artifacts:
        fields["artifact_id"] = event.artifacts[0].artifact_id
    if event.type.value in {"step_completed", "run_finished"}:
        fields["cost_usd"] = state.usage.cost_usd
    if isinstance(payload, RunFinished):
        fields["reason"] = payload.reason
        if fields["status"] == "failed":
            level = logging.ERROR
    log_event(
        event.type.value,
        level=level,
        run_id=event.run_id,
        step_number=event.step_number,
        span_id=event.span_id,
        sequence=event.sequence,
        **fields,
    )
