import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from time import monotonic, sleep
from typing import TypeVar
from uuid import UUID, uuid4

from miniagent.environments.managed_execution import current_cleanup_deadline
from miniagent.policy import ApprovalOutcome, ApprovalResolution, PolicyRequest
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalRequested,
    ApprovalResolved,
    CompactionCompleted,
    EventSequenceConflictError,
    NewRunEvent,
    RunEvent,
    RunEventType,
    ToolEnded,
    ToolExecutionStarted,
)
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.snapshots import (
    NewRunSnapshot,
    RunSnapshot,
    SnapshotVersionConflictError,
)
from miniagent.runtime.state import RunState
from miniagent.runtime.store_contracts import (
    ApprovalConflictError,
    ApprovalExpiredError,
    CommitResult,
    CorruptStoredDataError,
    CreateApproval,
    ResolveApproval,
    RunAlreadyExistsError,
    RunCommit,
    RunNotFoundError,
    RunRecord,
    StoredApproval,
    ToolResultFact,
    UnsupportedStorageVersionError,
)
from miniagent.storage.codecs import (
    decode_approval,
    decode_event,
    decode_run,
    decode_snapshot,
    encode_artifacts,
)
from miniagent.storage.codecs import (
    encode_datetime as _timestamp,
)
from miniagent.storage.codecs import (
    encode_model as _dump,
)
from miniagent.storage.queries.approvals import (
    insert_approval,
    select_approval,
    select_approval_for_run,
    select_approval_version,
    select_pending_approvals,
    select_uninvalidated_approvals,
)
from miniagent.storage.queries.approvals import (
    invalidate_approval as invalidate_approval_row,
)
from miniagent.storage.queries.approvals import (
    resolve_approval as resolve_approval_row,
)
from miniagent.storage.queries.events import insert_event, select_events
from miniagent.storage.queries.runs import (
    insert_run,
    latest_positions,
    run_exists,
    select_run,
    update_event_sequence,
    update_run,
    update_snapshot_version,
)
from miniagent.storage.queries.snapshots import (
    insert_snapshot,
    select_latest_snapshot,
    select_snapshot_version,
)

_SCHEMA_VERSION = 3
_T = TypeVar("_T")


def _now() -> datetime:
    return datetime.now(UTC)


class SQLiteRunStore:
    def __init__(
        self,
        path: Path,
        *,
        read_only: bool = False,
        busy_timeout_ms: int = 5_000,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")
        self.path = path.expanduser().resolve()
        self.read_only = read_only
        self.busy_timeout_ms = busy_timeout_ms
        self._clock = clock

    async def initialize(self) -> None:
        self._require_writable()
        await self._in_thread(self._initialize_sync)

    def create(
        self,
        state: RunState,
        *,
        recovery_context: RunRecoveryContext | None = None,
    ) -> Awaitable[RunRecord]:
        self._require_writable()
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
        self._require_writable()
        frozen = RunCommit.model_validate_json(change.model_dump_json())
        return self._in_thread(lambda: self._commit_sync(frozen))

    def append(
        self,
        event: NewRunEvent,
        *,
        expected_sequence: int,
    ) -> Awaitable[RunEvent]:
        self._require_writable()
        frozen = NewRunEvent.model_validate_json(event.model_dump_json())
        return self._in_thread(
            lambda: self._append_sync(frozen, expected_sequence=expected_sequence)
        )

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        types: tuple[RunEventType, ...] | None = None,
        limit: int | None = None,
    ) -> list[RunEvent]:
        if through_sequence is not None and through_sequence < 0:
            raise ValueError("through_sequence must be non-negative")
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        return await self._in_thread(
            lambda: self._list_events_sync(
                run_id,
                after_sequence=after_sequence,
                through_sequence=through_sequence,
                types=types,
                limit=limit,
            )
        )

    async def get_event(
        self, run_id: UUID, event_id: UUID, *, through_sequence: int
    ) -> RunEvent | None:
        if through_sequence < 0:
            raise ValueError("through_sequence must be non-negative")

        def get() -> RunEvent | None:
            with closing(self._connect()) as connection:
                self._require_run(connection, run_id)
                row = connection.execute(
                    "SELECT * FROM run_events WHERE run_id = ? AND event_id = ? AND sequence <= ?",
                    (str(run_id), str(event_id), through_sequence),
                ).fetchone()
                return decode_event(row) if row is not None else None

        return await self._in_thread(get)

    async def latest_sequence(self, run_id: UUID) -> int:
        return (await self.get(run_id)).last_event_sequence

    def save(
        self,
        snapshot: NewRunSnapshot,
        *,
        expected_version: int,
    ) -> Awaitable[RunSnapshot]:
        self._require_writable()
        frozen = NewRunSnapshot.model_validate_json(snapshot.model_dump_json())
        return self._in_thread(
            lambda: self._save_snapshot_sync(
                frozen,
                expected_version=expected_version,
            )
        )

    async def load_version(self, run_id: UUID, version: int) -> RunSnapshot | None:
        def load() -> RunSnapshot | None:
            with closing(self._connect()) as connection:
                row = select_snapshot_version(connection, run_id, version)
                return decode_snapshot(row) if row is not None else None

        return await self._in_thread(load)

    async def load_latest(self, run_id: UUID) -> RunSnapshot | None:
        return await self._in_thread(lambda: self._load_latest_sync(run_id))

    async def get_approval(self, approval_id: UUID) -> StoredApproval:
        return await self._in_thread(lambda: self._get_approval_sync(approval_id))

    async def list_pending_approvals(self) -> list[StoredApproval]:
        return await self._in_thread(self._list_pending_approvals_sync)

    async def list_tool_results(
        self, run_id: UUID, *, step_number: int
    ) -> list[ToolResultFact]:
        if step_number < 1:
            raise ValueError("step_number must be positive")

        def read() -> list[ToolResultFact]:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                record = self._get_record(connection, run_id)
                execution = record.state.execution
                if (
                    execution.current_step is not None
                    and execution.current_step.step_number == step_number
                    and execution.tool_batch is not None
                ):
                    calls = execution.tool_batch.calls
                    expected_count = execution.tool_batch.next_index
                else:
                    completed = next(
                        (
                            step
                            for step in record.state.conversation.completed_steps
                            if step.step_number == step_number
                        ),
                        None,
                    )
                    if completed is None:
                        raise CorruptStoredDataError(
                            "Tool result batch scope is unavailable"
                        )
                    messages = record.state.conversation.messages[
                        completed.message_start : completed.message_end
                    ]
                    calls = next(
                        (
                            message.tool_calls
                            for message in messages
                            if message.role == "assistant" and message.tool_calls
                        ),
                        (),
                    )
                    expected_count = len(calls)
                facts = []
                for row in select_events(
                    connection, run_id, after_sequence=0, limit=None
                ):
                    event = decode_event(row)
                    payload = event.payload
                    if (
                        event.step_number != step_number
                        or not isinstance(payload, ToolEnded)
                        or payload.result is None
                    ):
                        continue
                    if (
                        payload.call_index >= expected_count
                        or payload.tool_call_id != calls[payload.call_index].id
                        or payload.tool_name != calls[payload.call_index].name
                    ):
                        raise CorruptStoredDataError(
                            "Tool result scope does not match original batch"
                        )
                    facts.append(
                        ToolResultFact(
                            step_number=step_number,
                            call_index=payload.call_index,
                            tool_call_id=payload.tool_call_id,
                            result=payload.result,
                            event_sequence=event.sequence,
                        )
                    )
                if sorted(fact.call_index for fact in facts) != list(
                    range(expected_count)
                ):
                    raise CorruptStoredDataError(
                        "Tool results must uniquely cover committed batch prefix"
                    )
                return sorted(facts, key=lambda fact: fact.call_index)

        return await self._in_thread(read)

    async def list_unconsumed_approvals(self, run_id: UUID) -> list[StoredApproval]:
        def read() -> list[StoredApproval]:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN")
                self._get_record(connection, run_id)
                approvals = [
                    decode_approval(row)
                    for row in select_uninvalidated_approvals(connection, run_id)
                ]
                executions = [
                    decode_event(row)
                    for row in select_events(
                        connection, run_id, after_sequence=0, limit=None
                    )
                ]
                return [
                    stored
                    for stored in approvals
                    if not any(
                        isinstance(event.payload, ToolExecutionStarted)
                        and event.sequence
                        > next(
                            (
                                request_event.sequence
                                for request_event in executions
                                if request_event.event_id == stored.request_event_id
                            ),
                            0,
                        )
                        and event.step_number
                        == stored.approval.decision.request.step_number
                        and event.payload.call_index
                        == stored.approval.decision.request.call_index
                        and event.payload.tool_call_id
                        == stored.approval.decision.request.tool_call_id
                        and event.payload.tool_name
                        == stored.approval.decision.request.tool_name
                        for event in executions
                    )
                ]

        return await self._in_thread(read)

    async def _in_thread(self, operation: Callable[[], _T]) -> _T:
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("Run store is read-only")

    async def inspect_schema(self) -> None:
        def inspect() -> None:
            with closing(self._connect()):
                pass

        await self._in_thread(inspect)

    @staticmethod
    def _inspect_schema(connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != _SCHEMA_VERSION:
            raise UnsupportedStorageVersionError(
                f"Database schema version {version} is unsupported; expected {_SCHEMA_VERSION}"
            )

    def _connect(self, *, initializing: bool = False) -> sqlite3.Connection:
        mode = "ro" if self.read_only else ("rwc" if initializing else "rw")
        deadline = current_cleanup_deadline()
        if deadline is not None and deadline <= monotonic():
            raise TimeoutError("SQLite operation deadline exhausted before connection")
        busy_timeout = self.busy_timeout_ms
        if deadline is not None:
            busy_timeout = min(
                busy_timeout, max(0, int((deadline - monotonic()) * 1000))
            )
        connection = sqlite3.connect(
            self.path.as_uri() + f"?mode={mode}",
            uri=True,
            isolation_level=None,
            timeout=busy_timeout / 1_000,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout}")
            connection.set_progress_handler(
                lambda: int(
                    (remaining := current_cleanup_deadline()) is not None
                    and monotonic() >= remaining
                ),
                1000,
            )
            if not initializing:
                self._inspect_schema(connection)
            if self.read_only:
                connection.execute("PRAGMA query_only = ON")
            else:
                connection.execute("PRAGMA synchronous = FULL")
            return connection
        except BaseException:
            connection.close()
            raise

    def _initialize_sync(self) -> None:
        self._require_writable()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect(initializing=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version not in (0, _SCHEMA_VERSION):
                    raise UnsupportedStorageVersionError(
                        f"Database schema version {version} is unsupported; "
                        f"expected {_SCHEMA_VERSION}"
                    )
                if version == 0:
                    if (
                        connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                        ).fetchone()
                        is not None
                    ):
                        raise UnsupportedStorageVersionError(
                            "Database schema version 0 is nonempty and unsupported"
                        )
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
        self._require_writable()
        with closing(self._connect()) as connection:
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
            and recovery_context.workspace_spec.run_id != state.identity.run_id
        ):
            raise ValueError("Recovery context must belong to the current run")

        def create(connection: sqlite3.Connection) -> RunRecord:
            try:
                insert_run(
                    connection,
                    run_id=str(state.identity.run_id),
                    status=state.lifecycle.status.value,
                    state_json=_dump(state),
                    recovery_context_json=(
                        _dump(recovery_context) if recovery_context else None
                    ),
                    created_at=_timestamp(state.identity.created_at),
                    updated_at=_timestamp(state.lifecycle.updated_at),
                    finished_at=(
                        _timestamp(state.lifecycle.finished_at)
                        if state.lifecycle.finished_at
                        else None
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY:
                    raise RunAlreadyExistsError(state.identity.run_id) from exc
                raise
            return self._get_record(connection, state.identity.run_id)

        return self._transaction(create)

    def _get_sync(self, run_id: UUID) -> RunRecord:
        with closing(self._connect()) as connection:
            return self._get_record(connection, run_id)

    def _get_record(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
    ) -> RunRecord:
        row = select_run(connection, run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        return decode_run(row)

    def _commit_sync(self, change: RunCommit) -> CommitResult:
        def commit(connection: sqlite3.Connection) -> CommitResult:
            current = self._get_record(connection, change.state.identity.run_id)
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
            for event in events:
                if isinstance(event.payload, CompactionCompleted):
                    self._validate_compaction_commit(
                        connection, current, change, event, snapshot
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
                record=self._get_record(connection, change.state.identity.run_id),
                events=events,
                approvals=approvals,
                snapshot=snapshot,
            )

        return self._transaction(commit)

    def _validate_compaction_commit(
        self, connection, current, change, event, snapshot
    ) -> None:
        payload = event.payload
        if (
            len(change.events) != 1
            or snapshot is None
            or payload.new_snapshot_id != snapshot.snapshot_id
            or payload.new_snapshot_version != snapshot.version
            or payload.event_sequence != event.sequence
            or change.state.context.current_version != payload.current_version
            or payload.previous_version != current.state.context.current_version
            or payload.current_version != (payload.previous_version or 0) + 1
        ):
            raise ValueError("compaction commit reservation mismatch")
        row = select_snapshot_version(
            connection, event.run_id, payload.source_snapshot_version
        )
        source = decode_snapshot(row) if row is not None else None
        if (
            source is None
            or (
                payload.source_snapshot_id is not None
                and source.snapshot_id != payload.source_snapshot_id
            )
            or source.event_sequence != payload.source_event_sequence
        ):
            raise ValueError("compaction source snapshot mismatch")
        if payload.source_snapshot_id is None:
            raise ValueError("compaction source snapshot ID is required")
        messages = change.state.conversation.messages
        start, end = payload.covered_messages
        old = source.state.conversation.messages
        if (
            start != 2
            or not (start < end <= len(old))
            or messages[:2] != old[:2]
            or messages[3:] != old[end:]
            or sha256((messages[2].content or "").encode()).hexdigest()
            != payload.summary_sha256
        ):
            raise ValueError("compaction committed messages mismatch")
        covered = tuple(
            step.step_number
            for step in source.state.conversation.completed_steps
            if start <= step.message_start and step.message_end <= end
        )
        retained = tuple(
            step
            for step in source.state.conversation.completed_steps
            if step.message_start >= end
        )
        expected_ranges = tuple(
            step.model_copy(
                update={
                    "message_start": step.message_start - end + 3,
                    "message_end": step.message_end - end + 3,
                }
            )
            for step in retained
        )
        if (
            covered != payload.covered_steps
            or not retained
            or end != retained[0].message_start
            or retained[0].step_number != payload.retained_first_step
            or change.state.conversation.completed_steps != expected_ranges
        ):
            raise ValueError("compaction Step metadata mismatch")
        if (
            source.state.context.current_version != payload.previous_version
            or source.version != current.latest_snapshot_version
            or source.version >= snapshot.version
        ):
            raise ValueError("compaction source version mismatch")
        if (
            change.state.definition != current.state.definition
            or change.state.identity != current.state.identity
            or messages[:2] != current.state.conversation.messages[:2]
            or old != current.state.conversation.messages
        ):
            raise ValueError("compaction Run identity or source context mismatch")

    def _idempotent_resolution(
        self,
        connection: sqlite3.Connection,
        change: RunCommit,
    ) -> StoredApproval | None:
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
        row = select_approval_for_run(
            connection,
            change.state.identity.run_id,
            resolution_change.resolution.approval_id,
        )
        if row is None or row["resolution_json"] is None:
            return None
        record = decode_approval(row)
        if record.approval.resolution is not None and self._same_resolution(
            record.approval.resolution,
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
                sequence=sequence,
                occurred_at=occurred_at,
            )
            insert_event(
                connection,
                (
                    str(event.run_id),
                    stored_event.sequence,
                    str(stored_event.event_id),
                    event.schema_version,
                    event.type.value,
                    _timestamp(occurred_at),
                    _dump(event.payload),
                    encode_artifacts(event.artifacts),
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
    ) -> list[StoredApproval]:
        records: list[StoredApproval] = []
        for approval_change in change.approval_changes:
            if isinstance(approval_change, CreateApproval):
                event = self._approval_event(
                    events,
                    approval_change.request_event_id,
                )
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_REQUESTED,
                    approval_id=approval_change.request.approval_id,
                    request=approval_change.decision.request,
                )
                request = approval_change.request
                decision = approval_change.decision
                insert_approval(
                    connection,
                    (
                        str(request.approval_id),
                        str(request.run_id),
                        str(request.decision_id),
                        _dump(request),
                        _dump(decision),
                        _timestamp(request.requested_at),
                        _timestamp(request.expires_at),
                        str(event.event_id),
                    ),
                )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.identity.run_id,
                        request.approval_id,
                    )
                )
            elif isinstance(approval_change, ResolveApproval):
                event = self._approval_event(
                    events,
                    approval_change.resolution_event_id,
                )
                resolution = approval_change.resolution
                current = self._get_approval_for_run(
                    connection,
                    change.state.identity.run_id,
                    resolution.approval_id,
                )
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_RESOLVED,
                    approval_id=resolution.approval_id,
                    request=current.approval.decision.request,
                )
                if (
                    resolution.outcome is ApprovalOutcome.APPROVED
                    and self._clock() >= current.approval.request.expires_at
                ):
                    raise ApprovalExpiredError(resolution.approval_id)
                rowcount = resolve_approval_row(
                    connection,
                    (
                        resolution.outcome.value,
                        _dump(resolution),
                        str(event.event_id),
                        str(resolution.approval_id),
                        str(change.state.identity.run_id),
                        approval_change.expected_version,
                    ),
                )
                if rowcount != 1:
                    self._raise_approval_conflict(
                        connection,
                        change.state.identity.run_id,
                        resolution.approval_id,
                        approval_change.expected_version,
                    )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.identity.run_id,
                        resolution.approval_id,
                    )
                )
            else:
                event = self._approval_event(
                    events,
                    approval_change.invalidated_event_id,
                )
                self._validate_approval_event(
                    event,
                    expected_type=RunEventType.APPROVAL_INVALIDATED,
                    approval_id=approval_change.approval_id,
                )
                rowcount = invalidate_approval_row(
                    connection,
                    (
                        str(event.event_id),
                        str(approval_change.approval_id),
                        str(change.state.identity.run_id),
                        approval_change.expected_version,
                        ApprovalOutcome.APPROVED.value,
                    ),
                )
                if rowcount != 1:
                    self._raise_approval_conflict(
                        connection,
                        change.state.identity.run_id,
                        approval_change.approval_id,
                        approval_change.expected_version,
                    )
                records.append(
                    self._get_approval_for_run(
                        connection,
                        change.state.identity.run_id,
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
        request: PolicyRequest | None = None,
    ) -> None:
        if event.type is not expected_type:
            raise ValueError(f"Approval change requires {expected_type.value} event")
        payload = event.payload
        if (
            not isinstance(
                payload,
                (ApprovalRequested, ApprovalResolved, ApprovalInvalidated),
            )
            or payload.approval_id != approval_id
        ):
            raise ValueError("Approval event must reference the changed approval")
        if isinstance(payload, (ApprovalRequested, ApprovalResolved)):
            if request is None:
                raise ValueError("Approval event validation requires policy scope")
            if (
                event.step_number != request.step_number
                or payload.call_index != request.call_index
                or payload.tool_call_id != request.tool_call_id
                or payload.tool_name != request.tool_name
            ):
                raise ValueError("Approval event scope must match its policy request")

    @staticmethod
    def _approval_event(
        events: list[RunEvent],
        event_id: UUID,
    ) -> RunEvent:
        matches = [event for event in events if event.event_id == event_id]
        if len(matches) != 1:
            raise ValueError("Approval event ID must identify one committed event")
        return matches[0]

    def _raise_approval_conflict(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        approval_id: UUID,
        expected_version: int,
    ) -> None:
        row = select_approval_version(connection, run_id, approval_id)
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
        actual = self._latest_snapshot_version(
            connection,
            change.state.identity.run_id,
        )
        if actual != change.snapshot.expected_version:
            raise SnapshotVersionConflictError(
                expected=change.snapshot.expected_version,
                actual=actual,
            )
        expected_version = actual + 1
        snapshot = RunSnapshot(
            snapshot_id=change.snapshot.snapshot_id,
            version=expected_version,
            created_at=self._clock(),
            run_id=change.state.identity.run_id,
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
        rowcount = update_run(
            connection,
            run_id=str(state.identity.run_id),
            status=state.lifecycle.status.value,
            state_json=_dump(state),
            recovery_context_json=(
                _dump(recovery_context) if recovery_context else None
            ),
            last_event_sequence=last_event_sequence,
            latest_snapshot_version=latest_snapshot_version,
            updated_at=_timestamp(state.lifecycle.updated_at),
            finished_at=(
                _timestamp(state.lifecycle.finished_at)
                if state.lifecycle.finished_at
                else None
            ),
        )
        if rowcount != 1:
            raise RunNotFoundError(state.identity.run_id)

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
            update_event_sequence(connection, event.run_id, stored.sequence)
            return stored

        return self._transaction(append)

    def _list_events_sync(
        self,
        run_id: UUID,
        *,
        after_sequence: int,
        limit: int | None,
        through_sequence: int | None,
        types: tuple[RunEventType, ...] | None,
    ) -> list[RunEvent]:
        with closing(self._connect()) as connection:
            self._require_run(connection, run_id)
            rows = select_events(
                connection,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
                through_sequence=through_sequence,
                types=types,
            )
            return [decode_event(row) for row in rows]

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
            update_snapshot_version(connection, snapshot.run_id, stored.version)
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
        insert_snapshot(
            connection,
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
        with closing(self._connect()) as connection:
            self._require_run(connection, run_id)
            row = select_latest_snapshot(connection, run_id)
            return decode_snapshot(row) if row is not None else None

    def _latest_snapshot_version(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
    ) -> int:
        row = latest_positions(connection, run_id)
        if row is None:
            raise RunNotFoundError(run_id)
        return int(row["latest_snapshot_version"])

    def _get_approval_sync(self, approval_id: UUID) -> StoredApproval:
        with closing(self._connect()) as connection:
            return self._get_approval(connection, approval_id)

    def _get_approval(
        self,
        connection: sqlite3.Connection,
        approval_id: UUID,
    ) -> StoredApproval:
        row = select_approval(connection, approval_id)
        if row is None:
            raise ApprovalConflictError(f"Approval not found: {approval_id}")
        return decode_approval(row)

    def _get_approval_for_run(
        self,
        connection: sqlite3.Connection,
        run_id: UUID,
        approval_id: UUID,
    ) -> StoredApproval:
        row = select_approval_for_run(connection, run_id, approval_id)
        if row is None:
            raise ApprovalConflictError(
                f"Approval {approval_id} does not belong to run {run_id}"
            )
        return decode_approval(row)

    def _list_pending_approvals_sync(self) -> list[StoredApproval]:
        with closing(self._connect()) as connection:
            return [
                decode_approval(row) for row in select_pending_approvals(connection)
            ]

    def _require_run(self, connection: sqlite3.Connection, run_id: UUID) -> None:
        if not run_exists(connection, run_id):
            raise RunNotFoundError(run_id)
