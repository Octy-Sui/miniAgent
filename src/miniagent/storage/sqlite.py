import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from time import monotonic, sleep
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError

from miniagent.models import ArtifactRef
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime.events import (
    EventSequenceConflictError,
    NewRunEvent,
    RunEvent,
    RunEventType,
)
from miniagent.runtime.persistence import RunRecoveryContext
from miniagent.runtime.snapshots import (
    NewRunSnapshot,
    RunSnapshot,
    SnapshotVersionConflictError,
)
from miniagent.runtime.state import RunState
from miniagent.storage.models import (
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalRecord,
    CommitResult,
    CorruptStoredDataError,
    CreateApproval,
    ResolveApproval,
    RunAlreadyExistsError,
    RunCommit,
    RunNotFoundError,
    RunRecord,
    UnsupportedStorageVersionError,
)

_SCHEMA_VERSION = 2
_ARTIFACTS_ADAPTER = TypeAdapter(list[ArtifactRef])
_T = TypeVar("_T")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Stored datetimes must be timezone-aware")
    return (
        value.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _dump(model: BaseModel) -> str:
    return model.model_dump_json()


def _dump_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


class SQLiteRunStore:
    def __init__(
        self,
        path: Path,
        *,
        busy_timeout_ms: int = 5_000,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms
        self._clock = clock

    async def initialize(self) -> None:
        await self._in_thread(self._initialize_sync)

    def create(
        self,
        state: RunState,
        *,
        recovery_context: RunRecoveryContext | None = None,
    ) -> Awaitable[RunRecord]:
        frozen_state = RunState.model_validate_json(state.model_dump_json())
        frozen_context = (
            RunRecoveryContext.model_validate_json(recovery_context.model_dump_json())
            if recovery_context is not None
            else None
        )
        return self._in_thread(
            lambda: self._create_sync(
                frozen_state,
                recovery_context=frozen_context,
            )
        )

    async def get(self, run_id: UUID) -> RunRecord:
        return await self._in_thread(lambda: self._get_sync(run_id))

    def commit(self, change: RunCommit) -> Awaitable[CommitResult]:
        frozen = RunCommit.model_validate_json(change.model_dump_json())
        return self._in_thread(lambda: self._commit_sync(frozen))

    def append(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> Awaitable[RunEvent]:
        frozen = NewRunEvent.model_validate_json(event.model_dump_json())
        return self._in_thread(
            lambda: self._append_sync(frozen, expected_sequence=expected_sequence)
        )

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
    ) -> list[RunEvent]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        return await self._in_thread(
            lambda: self._list_events_sync(
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        )

    async def latest_sequence(self, run_id: UUID) -> int:
        return (await self.get(run_id)).last_event_sequence

    def save(
        self,
        snapshot: NewRunSnapshot,
        *,
        expected_version: int,
    ) -> Awaitable[RunSnapshot]:
        frozen = NewRunSnapshot.model_validate_json(snapshot.model_dump_json())
        return self._in_thread(
            lambda: self._save_snapshot_sync(
                frozen,
                expected_version=expected_version,
            )
        )

    async def load_latest(self, run_id: UUID) -> RunSnapshot | None:
        return await self._in_thread(lambda: self._load_latest_sync(run_id))

    async def get_approval(self, approval_id: UUID) -> ApprovalRecord:
        return await self._in_thread(lambda: self._get_approval_sync(approval_id))

    async def list_pending_approvals(self) -> list[ApprovalRecord]:
        return await self._in_thread(self._list_pending_approvals_sync)

    async def _in_thread(self, operation: Callable[[], _T]) -> _T:
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=self.busy_timeout_ms / 1_000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in (0, _SCHEMA_VERSION):
                    raise UnsupportedStorageVersionError(
                        f"Database schema version {version} is unsupported; "
                        f"expected {_SCHEMA_VERSION}"
                    )
                if version == 0:
                    schema = (
                        files("miniagent.storage.migrations")
                        .joinpath("001_initial.sql")
                        .read_text(encoding="utf-8")
                    )
                    self._execute_script(connection, schema)
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            self._enable_wal(connection)

    @staticmethod
    def _execute_script(connection: sqlite3.Connection, script: str) -> None:
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                sql = statement.strip()
                if sql:
                    connection.execute(sql)
                statement = ""
        if statement.strip():
            raise ValueError("SQLite schema ends with an incomplete statement")

    def _enable_wal(self, connection: sqlite3.Connection) -> None:
        deadline = monotonic() + self.busy_timeout_ms / 1_000
        while True:
            try:
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise sqlite3.OperationalError(
                        f"SQLite did not enable WAL mode: {mode}"
                    )
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or monotonic() >= deadline:
                    raise
                sleep(min(0.05, max(0.0, deadline - monotonic())))

    def _transaction(self, operation: Callable[[sqlite3.Connection], _T]) -> _T:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                connection.execute("COMMIT")
                return result
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def _create_sync(
        self,
        state: RunState,
        *,
        recovery_context: RunRecoveryContext | None,
    ) -> RunRecord:
        if (
            recovery_context is not None
            and recovery_context.workspace_spec.run_id != state.run_id
        ):
            raise ValueError("Recovery context must belong to the current run")

        def create(connection: sqlite3.Connection) -> RunRecord:
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, schema_version, status, state_json,
                        recovery_context_json, created_at, updated_at, finished_at
                    ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(state.run_id),
                        state.status.value,
                        _dump(state),
                        _dump(recovery_context) if recovery_context else None,
                        _timestamp(state.created_at),
                        _timestamp(state.updated_at),
                        _timestamp(state.finished_at) if state.finished_at else None,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if connection.execute(
                    "SELECT 1 FROM runs WHERE run_id = ?",
                    (str(state.run_id),),
                ).fetchone():
                    raise RunAlreadyExistsError(state.run_id) from exc
                raise
            return self._get_record(connection, state.run_id)

        return self._transaction(create)

    def _get_sync(self, run_id: UUID) -> RunRecord:
        with self._connect() as connection:
            return self._get_record(connection, run_id)

    def _get_record(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
    ) -> RunRecord:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        self._require_supported_data_version("run", row["schema_version"])
        try:
            state = RunState.model_validate_json(row["state_json"])
            context = (
                self._load_recovery_context(row["recovery_context_json"])
                if row["recovery_context_json"] is not None
                else None
            )
            if (
                state.run_id != run_id
                or state.status.value != row["status"]
                or _timestamp(state.created_at) != row["created_at"]
                or _timestamp(state.updated_at) != row["updated_at"]
                or (_timestamp(state.finished_at) if state.finished_at else None)
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

    def _commit_sync(self, change: RunCommit) -> CommitResult:
        def commit(connection: sqlite3.Connection) -> CommitResult:
            current = self._get_record(connection, change.state.run_id)
            idempotent = self._idempotent_resolution(connection, change)
            if idempotent is not None:
                return CommitResult(
                    record=current,
                    events=[],
                    approvals=[idempotent],
                    idempotent=True,
                )
            if current.last_event_sequence != change.expected_sequence:
                raise EventSequenceConflictError(
                    expected=change.expected_sequence,
                    actual=current.last_event_sequence,
                )

            events = self._insert_events(
                connection,
                change.events,
                start_sequence=change.expected_sequence + 1,
            )
            approvals = self._apply_approval_changes(
                connection,
                change,
                events,
            )
            snapshot = self._insert_commit_snapshot(
                connection,
                change,
                final_sequence=events[-1].sequence,
            )
            latest_snapshot_version = (
                snapshot.version
                if snapshot is not None
                else current.latest_snapshot_version
            )
            self._update_run(
                connection,
                state=change.state,
                recovery_context=change.recovery_context,
                last_event_sequence=events[-1].sequence,
                latest_snapshot_version=latest_snapshot_version,
            )
            return CommitResult(
                record=self._get_record(connection, change.state.run_id),
                events=events,
                approvals=approvals,
                snapshot=snapshot,
            )

        return self._transaction(commit)

    def _idempotent_resolution(
        self,
        connection: sqlite3.Connection,
        change: RunCommit,
    ) -> ApprovalRecord | None:
        resolution_change = next(
            (
                item
                for item in change.approval_changes
                if isinstance(item, ResolveApproval)
            ),
            None,
        )
        if resolution_change is None:
            return None
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ? AND run_id = ?",
            (
                str(resolution_change.resolution.approval_id),
                str(change.state.run_id),
            ),
        ).fetchone()
        if row is None or row["resolution_json"] is None:
            return None
        record = self._approval_from_row(row)
        if record.resolution is not None and self._same_resolution(
            record.resolution,
            resolution_change.resolution,
        ):
            return record
        raise ApprovalConflictError(
            f"Approval {resolution_change.resolution.approval_id} is already resolved"
        )

    @staticmethod
    def _same_resolution(
        stored: ApprovalResolution,
        retry: ApprovalResolution,
    ) -> bool:
        return (
            stored.approval_id,
            stored.outcome,
            stored.actor_kind,
            stored.actor_id,
            stored.reason,
        ) == (
            retry.approval_id,
            retry.outcome,
            retry.actor_kind,
            retry.actor_id,
            retry.reason,
        )

    def _insert_events(
        self,
        connection: sqlite3.Connection,
        events: list[NewRunEvent],
        *,
        start_sequence: int,
    ) -> list[RunEvent]:
        stored: list[RunEvent] = []
        for offset, event in enumerate(events):
            occurred_at = self._clock()
            sequence = start_sequence + offset
            stored_event = RunEvent(
                **event.model_dump(),
                event_id=uuid4(),
                sequence=sequence,
                occurred_at=occurred_at,
            )
            connection.execute(
                """
                INSERT INTO run_events (
                    run_id, sequence, event_id, schema_version, type,
                    occurred_at, payload_json, artifacts_json,
                    step_number, span_id, parent_span_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.run_id),
                    stored_event.sequence,
                    str(stored_event.event_id),
                    event.schema_version,
                    event.type.value,
                    _timestamp(occurred_at),
                    _dump_json(event.payload),
                    _ARTIFACTS_ADAPTER.dump_json(event.artifacts).decode(),
                    event.step_number,
                    event.span_id,
                    event.parent_span_id,
                ),
            )
            stored.append(stored_event)
        return stored

    def _apply_approval_changes(
        self,
        connection: sqlite3.Connection,
        change: RunCommit,
        events: list[RunEvent],
    ) -> list[ApprovalRecord]:
        records: list[ApprovalRecord] = []
        for approval_change in change.approval_changes:
            if isinstance(approval_change, CreateApproval):
                event = events[approval_change.request_event_index]
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_REQUESTED,
                    approval_id=approval_change.request.approval_id,
                )
                request = approval_change.request
                decision = approval_change.decision
                connection.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, run_id, decision_id, schema_version,
                        request_json, decision_json, requested_at, expires_at,
                        request_event_sequence
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(request.approval_id),
                        str(request.run_id),
                        str(request.decision_id),
                        _dump(request),
                        _dump(decision),
                        _timestamp(request.requested_at),
                        _timestamp(request.expires_at),
                        event.sequence,
                    ),
                )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.run_id,
                        request.approval_id,
                    )
                )
            elif isinstance(approval_change, ResolveApproval):
                event = events[approval_change.resolution_event_index]
                resolution = approval_change.resolution
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_RESOLVED,
                    approval_id=resolution.approval_id,
                )
                current = self._get_approval_for_run(
                    connection,
                    change.state.run_id,
                    resolution.approval_id,
                )
                if (
                    resolution.outcome is ApprovalOutcome.APPROVED
                    and self._clock() >= current.request.expires_at
                ):
                    raise ApprovalExpiredError(resolution.approval_id)
                cursor = connection.execute(
                    """
                    UPDATE approvals
                    SET outcome = ?, resolution_json = ?,
                        resolution_event_sequence = ?, version = version + 1
                    WHERE approval_id = ? AND run_id = ?
                        AND version = ? AND outcome IS NULL
                    """,
                    (
                        resolution.outcome.value,
                        _dump(resolution),
                        event.sequence,
                        str(resolution.approval_id),
                        str(change.state.run_id),
                        approval_change.expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_approval_conflict(
                        connection,
                        change.state.run_id,
                        resolution.approval_id,
                        approval_change.expected_version,
                    )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.run_id,
                        resolution.approval_id,
                    )
                )
            else:
                event = events[approval_change.invalidated_event_index]
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_INVALIDATED,
                    approval_id=approval_change.approval_id,
                )
                cursor = connection.execute(
                    """
                    UPDATE approvals
                    SET authorization_invalidated_event_sequence = ?,
                        version = version + 1
                    WHERE approval_id = ? AND run_id = ? AND version = ?
                        AND outcome = ?
                        AND authorization_invalidated_event_sequence IS NULL
                    """,
                    (
                        event.sequence,
                        str(approval_change.approval_id),
                        str(change.state.run_id),
                        approval_change.expected_version,
                        ApprovalOutcome.APPROVED.value,
                    ),
                )
                if cursor.rowcount != 1:
                    self._raise_approval_conflict(
                        connection,
                        change.state.run_id,
                        approval_change.approval_id,
                        approval_change.expected_version,
                    )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.run_id,
                        approval_change.approval_id,
                    )
                )
        return records

    def _validate_approval_event(
        self,
        event: RunEvent,
        *,
        expected_type: RunEventType,
        approval_id: UUID,
    ) -> None:
        if event.type is not expected_type:
            raise ValueError(f"Approval change requires {expected_type.value} event")
        if event.payload.get("approval_id") != str(approval_id):
            raise ValueError("Approval event must reference the changed approval")

    def _raise_approval_conflict(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        approval_id: UUID,
        expected_version: int,
    ) -> None:
        row = connection.execute(
            "SELECT version FROM approvals WHERE approval_id = ? AND run_id = ?",
            (str(approval_id), str(run_id)),
        ).fetchone()
        actual = row["version"] if row is not None else "missing"
        raise ApprovalConflictError(
            f"Expected approval {approval_id} version {expected_version}, "
            f"found {actual}"
        )

    def _insert_commit_snapshot(
        self,
        connection: sqlite3.Connection,
        change: RunCommit,
        *,
        final_sequence: int,
    ) -> RunSnapshot | None:
        if change.snapshot is None:
            return None
        actual = self._latest_snapshot_version(connection, change.state.run_id)
        if actual != change.snapshot.expected_version:
            raise SnapshotVersionConflictError(
                expected=change.snapshot.expected_version,
                actual=actual,
            )
        event = change.events[-1]
        expected_version = actual + 1
        if (
            event.type is not RunEventType.SNAPSHOT_CREATED
            or event.payload.get("snapshot_id") != str(change.snapshot.snapshot_id)
            or event.payload.get("version") != expected_version
        ):
            raise ValueError(
                "Snapshot commit requires a final snapshot_created event "
                "referencing its ID and version"
            )
        snapshot = RunSnapshot(
            snapshot_id=change.snapshot.snapshot_id,
            version=expected_version,
            created_at=self._clock(),
            run_id=change.state.run_id,
            event_sequence=final_sequence,
            state=change.state,
            recovery_context=change.recovery_context,
        )
        self._insert_snapshot(connection, snapshot)
        return snapshot

    def _update_run(
        self,
        connection: sqlite3.Connection,
        *,
        state: RunState,
        recovery_context: RunRecoveryContext | None,
        last_event_sequence: int,
        latest_snapshot_version: int,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE runs
            SET status = ?, state_json = ?, recovery_context_json = ?,
                last_event_sequence = ?, latest_snapshot_version = ?,
                updated_at = ?, finished_at = ?
            WHERE run_id = ?
            """,
            (
                state.status.value,
                _dump(state),
                _dump(recovery_context) if recovery_context else None,
                last_event_sequence,
                latest_snapshot_version,
                _timestamp(state.updated_at),
                _timestamp(state.finished_at) if state.finished_at else None,
                str(state.run_id),
            ),
        )
        if cursor.rowcount != 1:
            raise RunNotFoundError(state.run_id)

    def _append_sync(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> RunEvent:
        if expected_sequence < 0:
            raise ValueError("expected_sequence must be non-negative")

        def append(connection: sqlite3.Connection) -> RunEvent:
            record = self._get_record(connection, event.run_id)
            if record.last_event_sequence != expected_sequence:
                raise EventSequenceConflictError(
                    expected=expected_sequence,
                    actual=record.last_event_sequence,
                )
            stored = self._insert_events(
                connection,
                [event],
                start_sequence=expected_sequence + 1,
            )[0]
            connection.execute(
                "UPDATE runs SET last_event_sequence = ? WHERE run_id = ?",
                (stored.sequence, str(event.run_id)),
            )
            return stored

        return self._transaction(append)

    def _list_events_sync(
        self,
        run_id: UUID,
        *,
        after_sequence: int,
        limit: int | None,
    ) -> list[RunEvent]:
        with self._connect() as connection:
            self._require_run(connection, run_id)
            sql = (
                "SELECT * FROM run_events WHERE run_id = ? AND sequence > ? "
                "ORDER BY sequence ASC"
            )
            parameters: tuple[Any, ...] = (str(run_id), after_sequence)
            if limit is not None:
                sql += " LIMIT ?"
                parameters += (limit,)
            rows = connection.execute(sql, parameters).fetchall()
            return [self._event_from_row(row) for row in rows]

    def _event_from_row(self, row: sqlite3.Row) -> RunEvent:
        self._require_supported_data_version("event", row["schema_version"])
        try:
            return RunEvent(
                run_id=row["run_id"],
                schema_version=row["schema_version"],
                sequence=row["sequence"],
                event_id=row["event_id"],
                type=row["type"],
                occurred_at=row["occurred_at"],
                payload=json.loads(row["payload_json"]),
                artifacts=_ARTIFACTS_ADAPTER.validate_json(row["artifacts_json"]),
                step_number=row["step_number"],
                span_id=row["span_id"],
                parent_span_id=row["parent_span_id"],
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptStoredDataError(f"Invalid stored event: {exc}") from exc

    def _save_snapshot_sync(
        self,
        snapshot: NewRunSnapshot,
        *,
        expected_version: int,
    ) -> RunSnapshot:
        if expected_version < 0:
            raise ValueError("expected_version must be non-negative")

        def save(connection: sqlite3.Connection) -> RunSnapshot:
            record = self._get_record(connection, snapshot.run_id)
            actual = self._latest_snapshot_version(connection, snapshot.run_id)
            if actual != expected_version:
                raise SnapshotVersionConflictError(
                    expected=expected_version,
                    actual=actual,
                )
            if snapshot.event_sequence != record.last_event_sequence:
                raise EventSequenceConflictError(
                    expected=snapshot.event_sequence,
                    actual=record.last_event_sequence,
                )
            if (
                snapshot.state != record.state
                or snapshot.recovery_context != record.recovery_context
            ):
                raise ValueError("Snapshot must match the current persisted run")
            stored = RunSnapshot(
                **snapshot.model_dump(),
                snapshot_id=uuid4(),
                version=actual + 1,
                created_at=self._clock(),
            )
            self._insert_snapshot(connection, stored)
            connection.execute(
                "UPDATE runs SET latest_snapshot_version = ? WHERE run_id = ?",
                (stored.version, str(snapshot.run_id)),
            )
            return stored

        return self._transaction(save)

    def _insert_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: RunSnapshot,
    ) -> None:
        envelope = NewRunSnapshot(
            schema_version=snapshot.schema_version,
            run_id=snapshot.run_id,
            event_sequence=snapshot.event_sequence,
            state=snapshot.state,
            recovery_context=snapshot.recovery_context,
        )
        connection.execute(
            """
            INSERT INTO run_snapshots (
                run_id, version, snapshot_id, event_sequence,
                schema_version, snapshot_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(snapshot.run_id),
                snapshot.version,
                str(snapshot.snapshot_id),
                snapshot.event_sequence,
                snapshot.schema_version,
                _dump(envelope),
                _timestamp(snapshot.created_at),
            ),
        )

    def _load_latest_sync(self, run_id: UUID) -> RunSnapshot | None:
        with self._connect() as connection:
            self._require_run(connection, run_id)
            row = connection.execute(
                """
                SELECT * FROM run_snapshots
                WHERE run_id = ? ORDER BY version DESC LIMIT 1
                """,
                (str(run_id),),
            ).fetchone()
            return self._snapshot_from_row(row) if row is not None else None

    def _snapshot_from_row(self, row: sqlite3.Row) -> RunSnapshot:
        self._require_supported_data_version("snapshot", row["schema_version"])
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

    def _latest_snapshot_version(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
    ) -> int:
        row = connection.execute(
            "SELECT latest_snapshot_version FROM runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return int(row["latest_snapshot_version"])

    def _get_approval_sync(self, approval_id: UUID) -> ApprovalRecord:
        with self._connect() as connection:
            return self._get_approval(connection, approval_id)

    def _get_approval(
        self,
        connection: sqlite3.Connection,
        approval_id: UUID,
    ) -> ApprovalRecord:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (str(approval_id),),
        ).fetchone()
        if row is None:
            raise ApprovalConflictError(f"Approval not found: {approval_id}")
        return self._approval_from_row(row)

    def _get_approval_for_run(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRecord:
        row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ? AND run_id = ?",
            (str(approval_id), str(run_id)),
        ).fetchone()
        if row is None:
            raise ApprovalConflictError(
                f"Approval {approval_id} does not belong to run {run_id}"
            )
        return self._approval_from_row(row)

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord:
        self._require_supported_data_version("approval", row["schema_version"])
        try:
            record = ApprovalRecord(
                request=json.loads(row["request_json"]),
                decision=json.loads(row["decision_json"]),
                resolution=(
                    json.loads(row["resolution_json"])
                    if row["resolution_json"] is not None
                    else None
                ),
                version=row["version"],
                request_event_sequence=row["request_event_sequence"],
                resolution_event_sequence=row["resolution_event_sequence"],
                authorization_invalidated_event_sequence=row[
                    "authorization_invalidated_event_sequence"
                ],
            )
            if (
                str(record.request.approval_id) != row["approval_id"]
                or str(record.request.run_id) != row["run_id"]
                or str(record.decision.decision_id) != row["decision_id"]
                or _timestamp(record.request.requested_at) != row["requested_at"]
                or _timestamp(record.request.expires_at) != row["expires_at"]
                or (
                    record.resolution.outcome.value
                    if record.resolution is not None
                    else None
                )
                != row["outcome"]
            ):
                raise ValueError("Approval columns do not match stored payload")
            return record
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise CorruptStoredDataError(f"Invalid stored approval: {exc}") from exc

    def _list_pending_approvals_sync(self) -> list[ApprovalRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM approvals
                WHERE outcome IS NULL
                ORDER BY requested_at, approval_id
                """
            ).fetchall()
            return [self._approval_from_row(row) for row in rows]

    @staticmethod
    def _require_supported_data_version(kind: str, version: int) -> None:
        supported = 2 if kind == "event" else 1
        if version > supported:
            raise UnsupportedStorageVersionError(
                f"Stored {kind} schema version {version} is newer than supported "
                f"version {supported}"
            )
        if version < 1:
            raise CorruptStoredDataError(
                f"Stored {kind} schema version must be positive, found {version}"
            )

    @staticmethod
    def _load_recovery_context(value: str) -> RunRecoveryContext:
        raw = json.loads(value)
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if version != 2:
            raise UnsupportedStorageVersionError(
                f"Stored recovery context schema version {version} is unsupported"
            )
        return RunRecoveryContext.model_validate(raw)

    def _require_run(self, connection: sqlite3.Connection, run_id: UUID) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            is None
        ):
            raise RunNotFoundError(run_id)
