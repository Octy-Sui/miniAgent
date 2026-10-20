import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from pydantic import JsonValue

from miniagent.artifacts.contracts import ArtifactStore
from miniagent.runtime.events import RunEvent
from miniagent.runtime.history import iter_run_events
from miniagent.runtime.state import TERMINAL_STATUSES, RunStatus
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    RunRecord,
    RuntimeStore,
)
from miniagent.trajectory.contracts import (
    CoverageIssue,
    TrajectoryBoundary,
    TrajectoryDefinition,
    TrajectoryExportError,
    TrajectoryHeader,
)

MAX_JSON_BYTES = 67_108_864
EVENT_PAGE_SIZE = 16


def iter_json_bytes(value: JsonValue) -> Iterator[bytes]:
    encoder = json.JSONEncoder(
        ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    for part in encoder.iterencode(value):
        # A single JSON string may be large; avoid an additional whole UTF-8 copy.
        for offset in range(0, len(part), 65536):
            yield part[offset : offset + 65536].encode("utf-8")


class _Encoding:
    def __init__(self, deadline: float):
        self.content = bytearray()
        self.deadline = deadline

    def check(self) -> None:
        if monotonic() >= self.deadline:
            raise TrajectoryExportError(
                "TRAJECTORY_TIMEOUT", "Export deadline exceeded"
            )

    def append(self, chunk: bytes) -> None:
        self.check()
        if len(self.content) + len(chunk) > MAX_JSON_BYTES:
            raise TrajectoryExportError(
                "TRAJECTORY_TOO_LARGE", "Encoded JSON exceeds 67108864 bytes"
            )
        self.content.extend(chunk)

    async def value(self, value: JsonValue) -> None:
        for index, chunk in enumerate(iter_json_bytes(value)):
            self.append(chunk)
            if index % 16 == 0:
                await asyncio.sleep(0)


def _event_value(event: RunEvent) -> dict[str, JsonValue]:
    fields = _PAYLOAD_FIELDS.get(event.type.value)
    if fields is None or event.payload.__class__.__name__ != fields[0]:
        raise TrajectoryExportError("TRAJECTORY_STRUCTURE", "Unsupported event payload")
    payload = event.payload.model_dump(mode="json", include=set(fields[1]))
    return {
        "schema_version": event.schema_version,
        "run_id": str(event.run_id),
        "event_id": str(event.event_id),
        "sequence": event.sequence,
        "occurred_at": event.occurred_at.astimezone(UTC).isoformat(),
        "step_number": event.step_number,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "payload": payload,
        "artifacts": [ref.model_dump(mode="json") for ref in event.artifacts],
    }


async def encode_trajectory(
    *,
    record: RunRecord,
    definition: TrajectoryDefinition,
    store: RuntimeStore,
    artifacts: ArtifactStore,
    exported_at: datetime,
    producer_version: str,
    deadline: float,
) -> bytes:
    state = record.state
    run_id = state.identity.run_id
    if (
        definition.task != state.identity.task
        or definition.agent != state.definition.agent_spec
        or definition.tools != state.definition.tool_definitions
        or definition.budgets != state.definition.config
    ):
        raise TrajectoryExportError(
            "TRAJECTORY_STRUCTURE", "Definition differs from frozen Run"
        )
    if exported_at.tzinfo is None:
        raise TrajectoryExportError(
            "TRAJECTORY_STRUCTURE", "Export time must be timezone aware"
        )
    if record.latest_snapshot_version:
        snapshot = await store.load_version(run_id, record.latest_snapshot_version)
        if (
            snapshot is None
            or snapshot.run_id != run_id
            or snapshot.event_sequence > record.last_event_sequence
            or (
                snapshot.event_sequence == record.last_event_sequence
                and snapshot.state != state
            )
        ):
            raise TrajectoryExportError(
                "TRAJECTORY_STRUCTURE", "Snapshot and frozen summary boundary differ"
            )
        del snapshot
    terminal = state.lifecycle.status in TERMINAL_STATUSES
    header = TrajectoryHeader(
        run_id=run_id,
        trace_id=run_id.hex,
        producer_version=producer_version,
        exported_at=exported_at.astimezone(UTC),
        boundary=TrajectoryBoundary(
            through_sequence=record.last_event_sequence,
            status=state.lifecycle.status,
            terminal=terminal,
            snapshot_version=record.latest_snapshot_version,
            kind="terminal"
            if terminal
            else "suspended"
            if state.lifecycle.status == RunStatus.SUSPENDED
            else "abnormal",
        ),
        definition=definition,
        summary={
            "candidate_output": state.lifecycle.candidate_output,
            "final_output": state.lifecycle.final_output,
            "termination": state.lifecycle.termination_reason,
            "error": state.lifecycle.error.model_dump(mode="json")
            if state.lifecycle.error
            else None,
            "evaluation": state.execution.evaluation.model_dump(mode="json"),
            "usage": state.usage.model_dump(mode="json"),
            "unconfirmed": state.usage.unconfirmed_model_requests,
        },
    )
    encoding = _Encoding(deadline)
    value = header.model_dump(mode="json")
    encoding.append(b"{")
    for index, (key, item) in enumerate(value.items()):
        if index:
            encoding.append(b",")
        await encoding.value(key)
        encoding.append(b":")
        await encoding.value(item)
    encoding.append(b',"events":[')
    referenced: set[UUID] = set()
    issues: list[CoverageIssue] = []
    attempts: dict[str, tuple[str, int | None]] = {}
    responses: set[str] = set()
    commands: dict[str, tuple[int, UUID]] = {}
    outputs: set[str] = set()
    interrupted: set[str] = set()
    last_sequence = 0
    try:
        async for event in iter_run_events(
            store, run_id, through_sequence=record.last_event_sequence
        ):
            encoding.check()
            kind = event.type.value
            projected = _event_value(event)
            data = projected["payload"]
            assert isinstance(data, dict)
            attempt = str(data["attempt_id"]) if data.get("attempt_id") else None
            if kind in (
                "model_requested",
                "compaction_requested",
                "tool_execution_start",
                "candidate_submission_started",
                "diagnostic_finalization_started",
                "evaluation_start",
            ):
                if attempt is None or attempt in attempts:
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Missing or duplicate attempt identity"
                    )
                attempts[attempt] = (kind, event.step_number)
            elif kind in ("model_responded", "compaction_attempt_recorded"):
                expected = (
                    "model_requested"
                    if kind == "model_responded"
                    else "compaction_requested"
                )
                if (
                    attempt not in attempts
                    or attempts[attempt] != (expected, event.step_number)
                    or attempt in responses
                ):
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE",
                        "Response attempt conflicts with request",
                    )
                responses.add(attempt)
                if (
                    data.get("response") is None
                    and data.get("normalization_failure") is None
                ):
                    issues.append(
                        CoverageIssue(
                            code="RESPONSE_MISSING",
                            sequence=event.sequence,
                            attempt_id=UUID(attempt),
                            reason="No final response evidence was produced",
                        )
                    )
            elif kind == "operation_interrupted" and attempt:
                interrupted.add(attempt)
            elif kind == "tool_end":
                if attempt and (
                    attempt not in attempts
                    or attempts[attempt] != ("tool_execution_start", event.step_number)
                ):
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Tool attempt conflicts with execution"
                    )
                if (
                    data.get("observation") is None
                    and data.get("observation_missing_reason") != "interrupted"
                ):
                    issues.append(
                        CoverageIssue(
                            code="OBSERVATION_MISSING",
                            sequence=event.sequence,
                            attempt_id=UUID(attempt) if attempt else None,
                            reason=str(data.get("observation_missing_reason")),
                        )
                    )
            elif kind in (
                "operation_execution_identified",
                "diagnostic_finalization_execution_identified",
            ) and data.get("command"):
                identity = data["identity"]
                assert isinstance(identity, dict)
                if (
                    attempt is None
                    or attempt not in attempts
                    and kind == "operation_execution_identified"
                ):
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Command has no execution attempt"
                    )
                commands[str(identity["execution_id"])] = (
                    event.sequence,
                    UUID(attempt),
                )
            elif kind == "operation_output_recorded":
                output = data["output"]
                assert isinstance(output, dict)
                execution_id = str(output["execution_id"])
                if execution_id not in commands or execution_id in outputs:
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Output has no unique command"
                    )
                if str(output["attempt_id"]) != str(commands[execution_id][1]):
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Output attempt differs from command"
                    )
                outputs.add(execution_id)
                for stream in ("stdout", "stderr"):
                    evidence = output[stream]
                    assert isinstance(evidence, dict)
                    if evidence["status"] != "completed":
                        issues.append(
                            CoverageIssue(
                                code="STREAM_PARTIAL",
                                sequence=event.sequence,
                                attempt_id=UUID(str(output["attempt_id"])),
                                reason=f"{stream}: {evidence.get('reason')}",
                            )
                        )
            if kind == "run_finished" and data["status"] != state.lifecycle.status:
                raise TrajectoryExportError(
                    "TRAJECTORY_STRUCTURE", "Terminal event differs from summary"
                )
            if kind == "compaction_completed":
                source_version = data.get("source_snapshot_version")
                if isinstance(source_version, int):
                    snapshot = await store.load_version(run_id, source_version)
                    if (
                        snapshot is None
                        or snapshot.run_id != run_id
                        or snapshot.event_sequence > record.last_event_sequence
                        or str(snapshot.snapshot_id) != data.get("source_snapshot_id")
                    ):
                        raise TrajectoryExportError(
                            "TRAJECTORY_STRUCTURE", "Invalid compaction Snapshot source"
                        )
                    del snapshot
                source = data.get("source_event_sequence")
                if not isinstance(source, int) or source > record.last_event_sequence:
                    raise TrajectoryExportError(
                        "TRAJECTORY_STRUCTURE", "Compaction source exceeds boundary"
                    )
            referenced.update(ref.artifact_id for ref in event.artifacts)
            if last_sequence:
                encoding.append(b",")
            await encoding.value(projected)
            last_sequence = event.sequence
    except CorruptStoredDataError as exc:
        raise TrajectoryExportError("TRAJECTORY_STRUCTURE", str(exc)) from exc
    if last_sequence != record.last_event_sequence:
        raise TrajectoryExportError(
            "TRAJECTORY_STRUCTURE", "Event history differs from boundary"
        )
    for execution_id, (sequence, attempt_id) in commands.items():
        if execution_id not in outputs:
            issues.append(
                CoverageIssue(
                    code="OUTPUT_MISSING",
                    sequence=sequence,
                    attempt_id=attempt_id,
                    reason="Command output not recorded",
                )
            )
    encoding.append(b'],"artifacts":[')
    cursor = None
    count = 0
    catalog: set[UUID] = set()
    while True:
        encoding.check()
        page = await artifacts.list_records_for_run(run_id, cursor=cursor, limit=100)
        for artifact in page.records:
            if artifact.run_id != run_id:
                raise TrajectoryExportError(
                    "TRAJECTORY_STRUCTURE", "Artifact Run differs"
                )
            if artifact.type == "run_trajectory":
                continue
            catalog.add(artifact.artifact_id)
            if count:
                encoding.append(b",")
            entry = artifact.model_dump(
                mode="json",
                include={
                    "schema_version",
                    "artifact_id",
                    "run_id",
                    "type",
                    "name",
                    "media_type",
                    "uri",
                    "size_bytes",
                    "sha256",
                    "metadata",
                },
            )
            entry["relation"] = (
                "event_referenced"
                if artifact.artifact_id in referenced
                else "registered_only"
            )
            entry["body_verified"] = False
            await encoding.value(entry)
            count += 1
        if page.next_cursor is None:
            break
        if page.next_cursor == cursor:
            raise TrajectoryExportError(
                "TRAJECTORY_STRUCTURE", "Artifact cursor did not advance"
            )
        cursor = page.next_cursor
    for artifact_id in referenced - catalog:
        issues.append(
            CoverageIssue(
                code="ARTIFACT_MANIFEST_MISSING",
                reason=f"No registered manifest for {artifact_id}",
            )
        )
    encoding.append(b'],"coverage":')
    await encoding.value(
        {
            "status": "partial" if issues else "complete",
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "content_policy": "faithful",
            "not_collected": [
                "provider_reasoning_text",
                "http_headers",
                "credentials",
                "environment",
                "execution_permission_tokens",
                "provider_wire_payloads",
                "artifact_bodies",
            ],
            "unknown_requests": [
                attempt
                for attempt, (kind, _step) in attempts.items()
                if kind in ("model_requested", "compaction_requested")
                and attempt not in responses
            ],
        }
    )
    encoding.append(b"}")
    return bytes(encoding.content)


# Static payload allowlist: new fields and new event types require an explicit decision.
_PAYLOAD_FIELDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "operation_output_recorded": ("OperationOutputRecorded", ("type", "output")),
    "compaction_requested": (
        "CompactionRequested",
        (
            "request",
            "attempt_id",
            "hard_trigger",
            "type",
            "provider",
            "model",
            "attempt",
            "source_snapshot_version",
            "covered_messages",
        ),
    ),
    "compaction_attempt_recorded": (
        "CompactionAttemptRecorded",
        ("attempt_id", "response", "normalization_failure", "type", "result"),
    ),
    "compaction_failed": ("CompactionFailed", ("type", "code", "reason", "attempts")),
    "compaction_completed": (
        "CompactionCompleted",
        (
            "type",
            "previous_version",
            "current_version",
            "source_snapshot_version",
            "source_snapshot_id",
            "source_event_sequence",
            "new_snapshot_version",
            "new_snapshot_id",
            "event_sequence",
            "covered_messages",
            "covered_steps",
            "retained_first_step",
            "template_version",
            "summary_sha256",
            "provider",
            "model",
            "context_window_tokens",
            "output_reserve",
            "safety_margin",
            "business_budget",
            "summary_reserve",
            "old_input_tokens",
            "new_input_tokens",
            "summary_input_tokens",
            "hard_trigger",
            "attempts",
        ),
    ),
    "run_started": ("RunStarted", ("type",)),
    "run_suspended": (
        "RunSuspended",
        ("type", "interruption", "business_status", "phase"),
    ),
    "resume_started": (
        "ResumeStarted",
        ("resume_attempt_id", "source_sequence", "business_status", "phase", "type"),
    ),
    "resume_completed": (
        "ResumeCompleted",
        ("resume_attempt_id", "source_sequence", "business_status", "phase", "type"),
    ),
    "resume_failed": (
        "ResumeFailed",
        (
            "resume_attempt_id",
            "source_sequence",
            "business_status",
            "phase",
            "type",
            "code",
            "message",
        ),
    ),
    "provider_identity_changed": (
        "ProviderIdentityChanged",
        ("type", "original_endpoint_identity", "current_endpoint_identity"),
    ),
    "step_started": ("StepStarted", ("type",)),
    "step_completed": (
        "StepCompleted",
        ("type", "failed", "consecutive_failed_steps", "candidate_output"),
    ),
    "termination_decided": ("TerminationDecided", ("type", "decision")),
    "model_requested": (
        "ModelRequested",
        ("request", "attempt_id", "type", "provider", "model", "attempt"),
    ),
    "model_responded": (
        "ModelResponded",
        (
            "response",
            "normalization_failure",
            "request_cost",
            "cost_status",
            "disposition",
            "error",
            "attempt_id",
            "type",
            "finish_reason",
            "tool_call_count",
            "error_code",
        ),
    ),
    "tool_start": (
        "ToolStarted",
        ("call_index", "tool_call_id", "tool_name", "arguments", "type"),
    ),
    "tool_end": (
        "ToolEnded",
        (
            "call_index",
            "tool_call_id",
            "tool_name",
            "attempt_id",
            "raw_result",
            "return_validation",
            "observation",
            "observation_missing_reason",
            "type",
            "status",
            "error_code",
            "reason",
            "result",
        ),
    ),
    "tool_execution_start": (
        "ToolExecutionStarted",
        (
            "call_index",
            "tool_call_id",
            "tool_name",
            "canonical_arguments",
            "attempt_id",
            "type",
        ),
    ),
    "tool_execution_end": (
        "ToolExecutionEnded",
        (
            "call_index",
            "tool_call_id",
            "tool_name",
            "attempt_id",
            "type",
            "status",
            "error_code",
            "reason",
        ),
    ),
    "operation_execution_identified": (
        "OperationExecutionIdentified",
        ("command", "type", "attempt_id", "identity", "permission"),
    ),
    "operation_interrupted": (
        "OperationInterrupted",
        ("type", "attempt_id", "operation_kind", "unconfirmed"),
    ),
    "operation_execution_ended": (
        "OperationExecutionEnded",
        ("type", "attempt_id", "evidence"),
    ),
    "policy_decided": (
        "PolicyDecided",
        ("call_index", "tool_call_id", "tool_name", "type", "decision"),
    ),
    "approval_requested": (
        "ApprovalRequested",
        (
            "call_index",
            "tool_call_id",
            "tool_name",
            "type",
            "approval_id",
            "decision_id",
        ),
    ),
    "approval_resolved": (
        "ApprovalResolved",
        ("call_index", "tool_call_id", "tool_name", "type", "approval_id", "outcome"),
    ),
    "approval_invalidated": ("ApprovalInvalidated", ("type", "approval_id", "reason")),
    "evaluation_start": ("EvaluationStart", ("attempt_id", "type")),
    "diagnostic_finalization_started": (
        "DiagnosticFinalizationStarted",
        ("type", "kind", "attempt_id", "submission_id", "temporary_index_path"),
    ),
    "diagnostic_finalization_execution_identified": (
        "DiagnosticFinalizationExecutionIdentified",
        ("command", "type", "attempt_id", "identity", "permission"),
    ),
    "diagnostic_finalization_execution_ended": (
        "DiagnosticFinalizationExecutionEnded",
        ("type", "attempt_id", "evidence"),
    ),
    "diagnostic_finalization_completed": (
        "DiagnosticFinalizationCompleted",
        (
            "type",
            "attempt_id",
            "submission_id",
            "status",
            "patch_ref",
            "patch_sha256",
            "error",
        ),
    ),
    "diagnostic_finalization_interrupted": (
        "DiagnosticFinalizationInterrupted",
        ("type", "attempt_id", "reason", "execution_ended"),
    ),
    "candidate_submission_started": (
        "CandidateSubmissionStarted",
        (
            "type",
            "attempt_id",
            "submission_id",
            "candidate_sha256",
            "temporary_index_path",
        ),
    ),
    "candidate_submission_committed": (
        "CandidateSubmissionCommitted",
        (
            "type",
            "attempt_id",
            "submission_id",
            "candidate_sha256",
            "status",
            "patch_ref",
            "patch_sha256",
            "base_revision",
        ),
    ),
    "candidate_submission_failed": (
        "CandidateSubmissionFailed",
        ("type", "attempt_id", "submission_id", "candidate_sha256", "error"),
    ),
    "evaluation_result_recorded": (
        "EvaluationResultRecorded",
        ("type", "attempt_id", "result"),
    ),
    "evaluation_attempt_started": (
        "EvaluationAttemptStarted",
        (
            "type",
            "attempt_id",
            "harness_run_id",
            "instance_id",
            "task_digest",
            "patch_ref",
            "patch_sha256",
            "directory",
        ),
    ),
    "evaluation_attempt_interrupted": (
        "EvaluationAttemptInterrupted",
        ("type", "attempt_id", "reason"),
    ),
    "evaluation_completed": ("EvaluationCompleted", ("result", "type", "passed")),
    "run_finished": ("RunFinished", ("type", "status", "reason")),
}
