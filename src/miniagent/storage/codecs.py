import json
import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel, TypeAdapter, ValidationError

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.runtime.events import EventPayload, RunEvent
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.snapshots import NewRunSnapshot, RunSnapshot
from miniagent.runtime.state import RunState
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    RunRecord,
    StoredApproval,
    UnsupportedStorageVersionError,
)

_ARTIFACTS_ADAPTER = TypeAdapter(list[ArtifactRef])
_EVENT_PAYLOAD_ADAPTER = TypeAdapter(EventPayload)
_SUPPORTED_VERSIONS = {"run": 2, "event": 1, "snapshot": 2, "approval": 1}


def encode_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Stored datetimes must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def encode_model(model: BaseModel) -> str:
    return model.model_dump_json()


def encode_artifacts(artifacts: list[ArtifactRef]) -> str:
    return _ARTIFACTS_ADAPTER.dump_json(artifacts).decode()


def decode_run(row: sqlite3.Row) -> RunRecord:
    require_supported_version("run", row["schema_version"])
    run_id = row["run_id"]
    try:
        state = RunState.model_validate_json(row["state_json"])
        context = (
            decode_recovery_context(row["recovery_context_json"])
            if row["recovery_context_json"] is not None
            else None
        )
        if (
            str(state.identity.run_id) != run_id
            or state.lifecycle.status.value != row["status"]
            or encode_datetime(state.identity.created_at) != row["created_at"]
            or encode_datetime(state.lifecycle.updated_at) != row["updated_at"]
            or (
                encode_datetime(state.lifecycle.finished_at)
                if state.lifecycle.finished_at
                else None
            )
            != row["finished_at"]
        ):
            raise ValueError("Run projection does not match stored state")
        return RunRecord(
            state=state,
            last_event_sequence=row["last_event_sequence"],
            latest_snapshot_version=row["latest_snapshot_version"],
            recovery_context=context,
        )
    except (ValidationError, ValueError) as exc:
        raise CorruptStoredDataError(f"Invalid stored run {run_id}: {exc}") from exc


def decode_event(row: sqlite3.Row) -> RunEvent:
    require_supported_version("event", row["schema_version"])
    try:
        payload = _EVENT_PAYLOAD_ADAPTER.validate_json(row["payload_json"])
        if payload.type.value != row["type"]:
            raise ValueError("Event type column does not match stored payload")
        return RunEvent(
            run_id=row["run_id"],
            schema_version=row["schema_version"],
            sequence=row["sequence"],
            event_id=row["event_id"],
            occurred_at=row["occurred_at"],
            payload=payload,
            artifacts=_ARTIFACTS_ADAPTER.validate_json(row["artifacts_json"]),
            step_number=row["step_number"],
            span_id=row["span_id"],
            parent_span_id=row["parent_span_id"],
        )
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise CorruptStoredDataError(f"Invalid stored event: {exc}") from exc


def decode_snapshot(row: sqlite3.Row) -> RunSnapshot:
    require_supported_version("snapshot", row["schema_version"])
    try:
        envelope = NewRunSnapshot.model_validate_json(row["snapshot_json"])
        if (
            str(envelope.run_id) != row["run_id"]
            or envelope.event_sequence != row["event_sequence"]
            or envelope.schema_version != row["schema_version"]
        ):
            raise ValueError("Snapshot columns do not match stored payload")
        return RunSnapshot(
            **envelope.model_dump(),
            snapshot_id=row["snapshot_id"],
            version=row["version"],
            created_at=row["created_at"],
        )
    except (ValidationError, ValueError) as exc:
        raise CorruptStoredDataError(f"Invalid stored snapshot: {exc}") from exc


def decode_approval(row: sqlite3.Row) -> StoredApproval:
    require_supported_version("approval", row["schema_version"])
    try:
        record = StoredApproval(
            approval={
                "request": json.loads(row["request_json"]),
                "decision": json.loads(row["decision_json"]),
                "resolution": (
                    json.loads(row["resolution_json"])
                    if row["resolution_json"] is not None
                    else None
                ),
            },
            version=row["version"],
            request_event_id=row["request_event_id"],
            resolution_event_id=row["resolution_event_id"],
            authorization_invalidated_event_id=row[
                "authorization_invalidated_event_id"
            ],
        )
        if (
            str(record.approval.request.approval_id) != row["approval_id"]
            or str(record.approval.request.run_id) != row["run_id"]
            or str(record.approval.decision.decision_id) != row["decision_id"]
            or encode_datetime(record.approval.request.requested_at)
            != row["requested_at"]
            or encode_datetime(record.approval.request.expires_at) != row["expires_at"]
            or (
                record.approval.resolution.outcome.value
                if record.approval.resolution is not None
                else None
            )
            != row["outcome"]
        ):
            raise ValueError("Approval columns do not match stored payload")
        return record
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise CorruptStoredDataError(f"Invalid stored approval: {exc}") from exc


def decode_recovery_context(value: str) -> RunRecoveryContext:
    raw = json.loads(value)
    version = raw.get("schema_version") if isinstance(raw, dict) else None
    if version != 1:
        raise UnsupportedStorageVersionError(
            f"Stored recovery context schema version {version!r} is unsupported; "
            "expected 1"
        )
    return RunRecoveryContext.model_validate(raw)


def require_supported_version(kind: str, version: int) -> None:
    supported = _SUPPORTED_VERSIONS[kind]
    if version != supported:
        raise UnsupportedStorageVersionError(
            f"Stored {kind} schema version {version} is unsupported; "
            f"expected {supported}"
        )
