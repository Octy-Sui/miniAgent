import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.policy import ApprovalRequest
from miniagent.runtime.events import RunEvent, RunEventType
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import ToolsDisposition
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.snapshots import RunSnapshot
from miniagent.runtime.state import RunState
from miniagent.runtime.store_contracts import (
    CommitResult,
    RunCommit,
    RunRecord,
    RuntimeStore,
    StoredApproval,
    ToolResultFact,
)
from miniagent.storage.sqlite import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from tests.support.storage.sqlite import make_decision, make_state


async def waiting_case(
    path: Path,
    *,
    state: RunState | None = None,
) -> tuple[SQLiteRunStore, RunExecution, ApprovalRequest]:
    store = SQLiteRunStore(path)
    await store.initialize()
    state = state or make_state()
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=state.identity.run_id, store=store, recovery_context=None
            )
        ),
    )
    await execution.begin_step(provider="test", model="test-model")
    call = ToolCall(id="call-1", name="shell", arguments={"command": "pytest"})
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(tool_calls=(call,)),
            finish_reason=FinishReason.TOOL_CALL,
        ),
        None,
        disposition=ToolsDisposition(),
    )
    await execution.begin_tool(call_index=0, tool_call_id=call.id, tool_name="shell")
    decision = make_decision(execution.state)
    now = datetime.now(UTC)
    request = ApprovalRequest(
        approval_id=uuid4(),
        decision_id=decision.decision_id,
        run_id=state.identity.run_id,
        request_digest=decision.request_digest,
        policy_revision=decision.policy_revision,
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    await execution.open_approval(request, decision)
    return store, execution, request


class StoreBoundary:
    def __init__(
        self,
        store: RuntimeStore,
        *,
        action: Literal[
            "pause_after_commit", "applied_then_error", "not_dispatched_then_error"
        ],
        operation: Literal["create", "commit"] = "commit",
        event_type: RunEventType | None = None,
        channel: Connection | None = None,
        permission: str | None = None,
    ) -> None:
        if action not in {
            "pause_after_commit",
            "applied_then_error",
            "not_dispatched_then_error",
        }:
            raise ValueError("Unsupported boundary action")
        self.store = store
        self.action = action
        self.operation = operation
        self.event_type = event_type
        self.channel = channel
        self.permission = permission
        self.fired = False

    async def _invoke[T: (RunRecord, CommitResult)](
        self,
        operation: str,
        value: RunState | RunCommit,
        invoke: Callable[[], Awaitable[T]],
    ) -> T:

        matches = (
            not self.fired
            and operation == self.operation
            and (
                self.event_type is None
                or (
                    isinstance(value, RunCommit)
                    and any(
                        event.type == self.event_type
                        and (
                            self.permission is None
                            or getattr(event.payload, "permission", None)
                            == self.permission
                        )
                        for event in value.events
                    )
                )
            )
        )
        if not matches:
            return await invoke()
        self.fired = True
        result: T | None = None
        if self.action != "not_dispatched_then_error":
            result = await invoke()
        if self.channel is not None:
            self.channel.send(
                {
                    "boundary": operation,
                    "action": self.action,
                    "sequence": result.record.last_event_sequence
                    if isinstance(result, CommitResult)
                    else result.last_event_sequence
                    if result is not None
                    else None,
                }
            )
        if self.action == "pause_after_commit":
            if self.channel is None:
                raise ValueError("Pause boundary requires supervisor channel")
            if await asyncio.to_thread(self.channel.recv) != "continue":
                raise ValueError("Boundary supervisor did not permit continuation")
            assert result is not None
            return result
        state = value.state if isinstance(value, RunCommit) else value
        raise RunPersistenceError(
            run_id=state.identity.run_id,
            operation="boundary",
            last_confirmed_sequence=value.expected_sequence
            if isinstance(value, RunCommit)
            else None,
        )

    async def create(
        self, state: RunState, *, recovery_context: RunRecoveryContext | None = None
    ) -> RunRecord:
        return await self._invoke(
            "create",
            state,
            lambda: self.store.create(state, recovery_context=recovery_context),
        )

    async def commit(self, change: RunCommit) -> CommitResult:
        return await self._invoke("commit", change, lambda: self.store.commit(change))

    async def get(self, run_id: UUID) -> RunRecord:
        return await self.store.get(run_id)

    async def load_version(self, run_id: UUID, version: int) -> RunSnapshot | None:
        return await self.store.load_version(run_id, version)

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        types: tuple[RunEventType, ...] | None = None,
        limit: int | None = None,
    ) -> list[RunEvent]:
        return await self.store.list_events(
            run_id,
            after_sequence=after_sequence,
            through_sequence=through_sequence,
            types=types,
            limit=limit,
        )

    async def get_event(
        self, run_id: UUID, event_id: UUID, *, through_sequence: int
    ) -> RunEvent | None:
        return await self.store.get_event(
            run_id, event_id, through_sequence=through_sequence
        )

    async def list_tool_results(
        self, run_id: UUID, *, step_number: int, through_sequence: int | None = None
    ) -> list[ToolResultFact]:
        return await self.store.list_tool_results(
            run_id, step_number=step_number, through_sequence=through_sequence
        )

    async def get_approval(self, approval_id: UUID) -> StoredApproval:
        return await self.store.get_approval(approval_id)

    async def list_unconsumed_approvals(self, run_id: UUID) -> list[StoredApproval]:
        return await self.store.list_unconsumed_approvals(run_id)
