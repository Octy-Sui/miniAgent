import asyncio
from collections.abc import Sequence
from uuid import UUID, uuid4

from miniagent.runtime.event_logging import log_committed_event
from miniagent.runtime.events import NewRunEvent
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.state import RunState
from miniagent.runtime.store_contracts import (
    ApprovalChange,
    CommitResult,
    RunCommit,
    RunRecord,
    RuntimeStore,
    SnapshotChange,
)


class RunPersistenceError(RuntimeError):
    def __init__(
        self,
        *,
        run_id: UUID,
        operation: str,
        last_confirmed_sequence: int | None,
    ) -> None:
        self.run_id = run_id
        self.operation = operation
        self.last_confirmed_sequence = last_confirmed_sequence
        super().__init__(
            f"Failed to persist run {run_id} during {operation}; "
            f"last confirmed event sequence is {last_confirmed_sequence}"
        )


class RunRecorder:
    def __init__(
        self,
        *,
        run_id: UUID,
        store: RuntimeStore | None,
        recovery_context: RunRecoveryContext | None,
    ) -> None:
        if store is None and recovery_context is not None:
            raise ValueError("Recovery context requires a run store")
        self.run_id = run_id
        self.store = store
        self.recovery_context = recovery_context
        self.record: RunRecord | None = None

    @classmethod
    def attach(cls, store: RuntimeStore, record: RunRecord) -> "RunRecorder":
        record = RunRecord.model_validate_json(record.model_dump_json())
        recorder = cls(
            run_id=record.state.identity.run_id,
            store=store,
            recovery_context=record.recovery_context,
        )
        recorder.record = record
        return recorder

    async def create(self, state: RunState) -> None:
        if self.store is None:
            return
        try:
            self.record = await self.store.create(
                state,
                recovery_context=self.recovery_context,
            )
        except (Exception, asyncio.CancelledError) as exc:
            raise RunPersistenceError(
                run_id=self.run_id,
                operation="create",
                last_confirmed_sequence=None,
            ) from exc

    async def commit(
        self,
        state: RunState,
        *,
        events: Sequence[NewRunEvent],
        approval_changes: Sequence[ApprovalChange] = (),
        snapshot: bool = False,
        snapshot_change: SnapshotChange | None = None,
    ) -> CommitResult | None:
        if self.store is None:
            return None
        if self.record is None:
            raise RuntimeError("Run recorder has not been created")
        if state.identity.run_id != self.run_id:
            raise ValueError("Recorded state belongs to another run")

        pending_events = list(events)
        if snapshot and snapshot_change is None:
            snapshot_id = uuid4()
            snapshot_change = SnapshotChange(
                snapshot_id=snapshot_id,
                expected_version=self.record.latest_snapshot_version,
            )
        if not pending_events:
            raise ValueError("A persisted run commit requires at least one event")

        try:
            result = await self.store.commit(
                RunCommit(
                    state=state,
                    events=pending_events,
                    expected_sequence=self.record.last_event_sequence,
                    recovery_context=self.recovery_context,
                    approval_changes=list(approval_changes),
                    snapshot=snapshot_change,
                )
            )
        except (Exception, asyncio.CancelledError) as exc:
            raise RunPersistenceError(
                run_id=self.run_id,
                operation="commit",
                last_confirmed_sequence=self.record.last_event_sequence,
            ) from exc
        if result.idempotent:
            raise RunPersistenceError(
                run_id=self.run_id,
                operation="unexpected idempotent commit",
                last_confirmed_sequence=self.record.last_event_sequence,
            )
        self.record = result.record
        for event in result.events:
            log_committed_event(event, result.record.state)
        return result
