import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.agents import AgentSpec
from miniagent.environments import (
    LocalOptions,
    LocalWorkspaceSpec,
)
from miniagent.environments.contracts import WorkspaceDelta
from miniagent.environments.persistence import (
    EnvironmentReference,
    persist_workspace_spec,
)
from miniagent.models.config import ModelTarget
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
)
from miniagent.runtime import (
    EventSequenceConflictError,
    NewRunEvent,
    NewRunSnapshot,
    RunCursor,
    RunEventType,
    RunRecoveryContext,
    RunState,
    RunStatus,
    RuntimeConfig,
    SnapshotVersionConflictError,
    create_run_state,
)
from miniagent.runtime.events import (
    ModelResponded,
    ToolEnded,
)
from miniagent.runtime.progress import CurrentStep, ExecutionPhase
from miniagent.runtime.state import ToolBatch
from miniagent.runtime.store_contracts import (
    ApprovalConflictError,
    ApprovalExpiredError,
    CreateApproval,
    InvalidateApproval,
    ResolveApproval,
    RunCommit,
    SnapshotChange,
    UnsupportedStorageVersionError,
)
from miniagent.runtime.transitions import (
    prepare,
    start_running,
    wait_for_approval,
)
from miniagent.runtime.transitions import (
    resolve_approval as transition_from_approval,
)
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import (
    ToolCall,
    ToolResult,
    ToolReturnValidation,
    ToolStatus,
)
from tests.support.storage.sqlite import (
    approval_event_id,
    create_approval,
    make_decision,
    make_event,
    make_state,
)


async def test_event_sequences_are_contiguous_and_scoped_to_run(
    store: SQLiteRunStore,
) -> None:
    first = make_state()
    second = make_state()
    await store.create(first)
    await store.create(second)

    new_first_event = make_event(
        run_id=first.identity.run_id,
        type=RunEventType.RUN_STARTED,
    )
    first_event = await store.append(
        new_first_event,
        expected_sequence=0,
    )
    second_event = await store.append(
        make_event(run_id=second.identity.run_id, type=RunEventType.RUN_STARTED),
        expected_sequence=0,
    )

    assert first_event.sequence == 1
    assert first_event.event_id == new_first_event.event_id
    assert second_event.sequence == 1
    assert await store.latest_sequence(first.identity.run_id) == 1


async def test_tool_end_round_trips_full_result_after_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(path)
    await store.initialize()
    state = make_state()
    await store.create(state)
    result = ToolResult(
        tool_call_id="call-1",
        status=ToolStatus.SUCCESS,
        data={"text": "complete output", "count": 2},
        duration_ms=37,
        metrics={"exit_code": 0},
        workspace_delta=WorkspaceDelta(files_modified=["src/app.py"]),
    )
    event = NewRunEvent(
        run_id=state.identity.run_id,
        payload=ToolEnded(
            call_index=0,
            tool_call_id="call-1",
            tool_name="shell",
            status=result.status.value,
            result=result,
            raw_result=None,
            return_validation=ToolReturnValidation(
                disposition="not_returned", reason="fixture_not_returned"
            ),
            response_error="fixture_not_observed",
        ),
        step_number=1,
        span_id="2222222222222222",
    )
    await store.append(event, expected_sequence=0)

    reopened = SQLiteRunStore(path)
    await reopened.initialize()
    stored = (await reopened.list_events(state.identity.run_id))[0]

    assert stored.event_id == event.event_id
    assert isinstance(stored.payload, ToolEnded)
    assert stored.payload.result == result


async def test_concurrent_append_with_same_expected_sequence_has_one_winner(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    events = [
        make_event(run_id=state.identity.run_id, type=RunEventType.MODEL_REQUESTED),
        make_event(run_id=state.identity.run_id, type=RunEventType.MODEL_RESPONDED),
    ]

    results = await asyncio.gather(
        *(store.append(event, expected_sequence=0) for event in events),
        return_exceptions=True,
    )

    assert len([result for result in results if not isinstance(result, Exception)]) == 1
    conflict = next(result for result in results if isinstance(result, Exception))
    assert isinstance(conflict, EventSequenceConflictError)
    assert [
        event.sequence for event in await store.list_events(state.identity.run_id)
    ] == [1]


async def test_commit_atomically_updates_state_context_events_and_snapshot(
    store: SQLiteRunStore,
    tmp_path: Path,
) -> None:
    state = make_state()
    await store.create(state)
    updated = state.model_copy(deep=True)
    updated = prepare(updated)
    context = RunRecoveryContext(
        workspace_id="workspace-1",
        workspace_spec=persist_workspace_spec(
            LocalWorkspaceSpec(
                run_id=state.identity.run_id,
                options=LocalOptions(source_dir=tmp_path),
            )
        ),
        policy_revision="policy-v1",
    )
    snapshot_id = uuid4()

    result = await store.commit(
        RunCommit(
            state=updated,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.RUN_STARTED,
                ),
            ],
            expected_sequence=0,
            recovery_context=context,
            snapshot=SnapshotChange(snapshot_id=snapshot_id, expected_version=0),
        )
    )

    assert [event.sequence for event in result.events] == [1]
    assert result.record.state.lifecycle.status is RunStatus.PREPARING
    assert result.record.recovery_context == context
    assert result.record.last_event_sequence == 1
    assert result.snapshot is not None
    assert result.snapshot.event_sequence == 1
    assert result.snapshot.recovery_context == context
    assert await store.load_latest(state.identity.run_id) == result.snapshot


async def test_snapshot_requires_current_semantic_state(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    equivalent = RunState.model_validate_json(state.model_dump_json())

    snapshot = await store.save(
        NewRunSnapshot(
            run_id=state.identity.run_id,
            event_sequence=0,
            state=equivalent,
        ),
        expected_version=0,
    )

    assert snapshot.version == 1
    with pytest.raises(SnapshotVersionConflictError):
        await store.save(
            NewRunSnapshot(
                run_id=state.identity.run_id,
                event_sequence=0,
                state=equivalent,
            ),
            expected_version=0,
        )


async def test_approval_resolution_is_atomic_and_idempotent(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    decision = make_decision(state)
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
    running = start_running(prepare(state))
    running = RunState.model_validate(
        running.model_copy(
            update={
                "execution": running.execution.model_copy(
                    update={
                        "cursor": RunCursor.EXECUTE_TOOL_CALLS,
                        "phase": ExecutionPhase.EXECUTE_TOOLS,
                        "steps_started": 1,
                        "current_step": CurrentStep(
                            step_number=1,
                            message_start=0,
                            started_event_id=uuid4(),
                            started_sequence=1,
                        ),
                        "tool_batch": ToolBatch(
                            calls=(
                                ToolCall(
                                    id=decision.request.tool_call_id,
                                    name=decision.request.tool_name,
                                    arguments=decision.request.arguments,
                                ),
                            ),
                        ),
                    }
                )
            }
        ).model_dump(mode="python")
    )
    waiting = wait_for_approval(running, request, decision)
    created = await store.commit(
        RunCommit(
            state=waiting,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={
                        "approval_id": str(request.approval_id),
                        "decision_id": str(decision.decision_id),
                    },
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_id=approval_event_id(
                        state.identity.run_id,
                        request.approval_id,
                        RunEventType.APPROVAL_REQUESTED,
                    ),
                )
            ],
        )
    )
    assert [
        item.approval.request.approval_id
        for item in await store.list_pending_approvals()
    ] == [request.approval_id]
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="operator",
        reason="Reviewed.",
        resolved_at=datetime.now(UTC),
    )
    resolved_state = transition_from_approval(waiting, resolution)
    change = RunCommit(
        state=resolved_state,
        events=[
            make_event(
                run_id=state.identity.run_id,
                type=RunEventType.APPROVAL_RESOLVED,
                payload={"approval_id": str(request.approval_id)},
            )
        ],
        expected_sequence=created.record.last_event_sequence,
        approval_changes=[
            ResolveApproval(
                resolution=resolution,
                expected_version=1,
                resolution_event_id=approval_event_id(
                    state.identity.run_id,
                    resolution.approval_id,
                    RunEventType.APPROVAL_RESOLVED,
                ),
            )
        ],
    )

    first = await store.commit(change)
    repeated = await store.commit(change)

    assert first.approvals[0].version == 2
    assert repeated.idempotent
    assert repeated.events == []
    assert repeated.record.last_event_sequence == first.record.last_event_sequence
    assert await store.list_pending_approvals() == []


async def test_conflicting_approval_resolution_rolls_back_event(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    decision = make_decision(state)
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
    await store.commit(
        RunCommit(
            state=state,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={
                        "approval_id": str(request.approval_id),
                        "decision_id": str(decision.decision_id),
                    },
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_id=approval_event_id(
                        state.identity.run_id,
                        request.approval_id,
                        RunEventType.APPROVAL_REQUESTED,
                    ),
                )
            ],
        )
    )
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.REJECTED,
        actor_kind="human",
        actor_id="operator",
        reason="Rejected.",
        resolved_at=datetime.now(UTC),
    )

    with pytest.raises(ApprovalConflictError):
        await store.commit(
            RunCommit(
                state=state,
                events=[
                    make_event(
                        run_id=state.identity.run_id,
                        type=RunEventType.APPROVAL_RESOLVED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=1,
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=2,
                        resolution_event_id=approval_event_id(
                            state.identity.run_id,
                            resolution.approval_id,
                            RunEventType.APPROVAL_RESOLVED,
                        ),
                    )
                ],
            )
        )

    assert await store.latest_sequence(state.identity.run_id) == 1
    assert len(await store.list_events(state.identity.run_id)) == 1


async def test_store_reopens_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    state = make_state()
    first = SQLiteRunStore(path)
    await first.initialize()
    await first.create(state)
    await first.append(
        make_event(run_id=state.identity.run_id, type=RunEventType.RUN_STARTED),
        expected_sequence=0,
    )

    reopened = SQLiteRunStore(path)
    await reopened.initialize()

    assert (await reopened.get(state.identity.run_id)).last_event_sequence == 1
    assert (await reopened.list_events(state.identity.run_id))[
        0
    ].type is RunEventType.RUN_STARTED


async def test_event_trace_scope_round_trips(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)

    stored = await store.append(
        make_event(
            run_id=state.identity.run_id,
            type=RunEventType.STEP_STARTED,
            step_number=1,
            span_id="2222222222222222",
            parent_span_id="1111111111111111",
        ),
        expected_sequence=0,
    )

    assert stored.step_number == 1
    assert stored.span_id == "2222222222222222"
    assert stored.parent_span_id == "1111111111111111"


async def test_approval_resolution_and_invalidation_are_scoped_to_run(
    store: SQLiteRunStore,
) -> None:
    owner = make_state()
    other = make_state()
    await store.create(owner)
    await store.create(other)
    request, owner_sequence = await create_approval(
        store,
        owner,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="operator",
        reason="Reviewed.",
        resolved_at=datetime.now(UTC),
    )
    cross_run_resolution = RunCommit(
        state=other,
        events=[
            make_event(
                run_id=other.identity.run_id,
                type=RunEventType.APPROVAL_RESOLVED,
                payload={"approval_id": str(request.approval_id)},
            )
        ],
        expected_sequence=0,
        approval_changes=[
            ResolveApproval(
                resolution=resolution,
                expected_version=1,
                resolution_event_id=approval_event_id(
                    other.identity.run_id,
                    resolution.approval_id,
                    RunEventType.APPROVAL_RESOLVED,
                ),
            )
        ],
    )

    with pytest.raises(ApprovalConflictError, match="does not belong"):
        await store.commit(cross_run_resolution)
    assert await store.latest_sequence(other.identity.run_id) == 0

    resolved = await store.commit(
        RunCommit(
            state=owner,
            events=[
                make_event(
                    run_id=owner.identity.run_id,
                    type=RunEventType.APPROVAL_RESOLVED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=owner_sequence,
            approval_changes=[
                ResolveApproval(
                    resolution=resolution,
                    expected_version=1,
                    resolution_event_id=approval_event_id(
                        owner.identity.run_id,
                        resolution.approval_id,
                        RunEventType.APPROVAL_RESOLVED,
                    ),
                )
            ],
        )
    )
    with pytest.raises(ApprovalConflictError):
        await store.commit(cross_run_resolution)
    with pytest.raises(ApprovalConflictError):
        await store.commit(
            RunCommit(
                state=other,
                events=[
                    make_event(
                        run_id=other.identity.run_id,
                        type=RunEventType.APPROVAL_INVALIDATED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=0,
                approval_changes=[
                    InvalidateApproval(
                        approval_id=request.approval_id,
                        expected_version=resolved.approvals[0].version,
                        invalidated_event_id=approval_event_id(
                            other.identity.run_id,
                            request.approval_id,
                            RunEventType.APPROVAL_INVALIDATED,
                        ),
                    )
                ],
            )
        )

    assert await store.latest_sequence(other.identity.run_id) == 0
    assert (await store.get_approval(request.approval_id)).version == 2


async def test_expired_approval_cannot_be_backdated(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 9, 17, 12, tzinfo=UTC)
    store = SQLiteRunStore(tmp_path / "runs.sqlite3", clock=lambda: now)
    await store.initialize()
    state = make_state()
    await store.create(state)
    request, sequence = await create_approval(
        store,
        state,
        expires_at=now - timedelta(seconds=1),
    )
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="operator",
        reason="Reviewed.",
        resolved_at=request.expires_at - timedelta(seconds=1),
    )

    with pytest.raises(ApprovalExpiredError):
        await store.commit(
            RunCommit(
                state=state,
                events=[
                    make_event(
                        run_id=state.identity.run_id,
                        type=RunEventType.APPROVAL_RESOLVED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=sequence,
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=1,
                        resolution_event_id=approval_event_id(
                            state.identity.run_id,
                            resolution.approval_id,
                            RunEventType.APPROVAL_RESOLVED,
                        ),
                    )
                ],
            )
        )

    assert await store.latest_sequence(state.identity.run_id) == sequence
    assert (await store.get_approval(request.approval_id)).approval.resolution is None


async def test_idempotent_resolution_ignores_retry_timestamp(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    request, sequence = await create_approval(
        store,
        state,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    original = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="operator",
        reason="Reviewed.",
        resolved_at=datetime.now(UTC),
    )

    def resolution_commit(resolution: ApprovalResolution) -> RunCommit:
        return RunCommit(
            state=state,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.APPROVAL_RESOLVED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=sequence,
            approval_changes=[
                ResolveApproval(
                    resolution=resolution,
                    expected_version=1,
                    resolution_event_id=approval_event_id(
                        state.identity.run_id,
                        resolution.approval_id,
                        RunEventType.APPROVAL_RESOLVED,
                    ),
                )
            ],
        )

    await store.commit(resolution_commit(original))
    retry = await store.commit(
        resolution_commit(
            original.model_copy(
                update={"resolved_at": original.resolved_at + timedelta(seconds=5)}
            )
        )
    )

    assert retry.idempotent
    assert retry.approvals[0].approval.resolution == original


async def test_recovery_context_never_serializes_environment_values(
    store: SQLiteRunStore,
    tmp_path: Path,
) -> None:
    state = make_state()
    secret = "credential-value-that-must-not-be-stored"
    runtime_spec = LocalWorkspaceSpec(
        run_id=state.identity.run_id,
        options=LocalOptions(source_dir=tmp_path),
        environment_values={"API_TOKEN": secret},
    )
    with pytest.raises(ValueError, match="persistent reference"):
        persist_workspace_spec(runtime_spec)
    with pytest.raises(ValidationError, match="sanitized"):
        RunRecoveryContext(
            workspace_id="workspace-1",
            workspace_spec=runtime_spec,
        )
    context = RunRecoveryContext(
        workspace_id="workspace-1",
        workspace_spec=persist_workspace_spec(
            runtime_spec,
            environment_refs={
                "API_TOKEN": EnvironmentReference(key="MINIAGENT_API_TOKEN")
            },
        ),
    )

    await store.create(state, recovery_context=context)
    snapshot_id = uuid4()
    await store.commit(
        RunCommit(
            state=state,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.RUN_STARTED,
                )
            ],
            expected_sequence=0,
            recovery_context=context,
            snapshot=SnapshotChange(
                snapshot_id=snapshot_id,
                expected_version=0,
            ),
        )
    )

    assert secret.encode() not in store.path.read_bytes()


async def test_commit_freezes_nested_input_before_dispatch(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    other = make_state()
    await store.create(state)
    await store.create(other)
    event = make_event(
        run_id=state.identity.run_id,
        type=RunEventType.MODEL_RESPONDED,
    )
    change = RunCommit(state=state, events=[event], expected_sequence=0)

    operation = store.commit(change)
    change.events.append(
        make_event(run_id=other.identity.run_id, type=RunEventType.MODEL_REQUESTED)
    )
    result = await operation

    assert len(result.events) == 1
    assert result.events[0].payload == ModelResponded(
        finish_reason="stop",
        tool_call_count=0,
    )
    assert await store.latest_sequence(other.identity.run_id) == 0


async def test_concurrent_initialization_serializes_migration(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    stores = [SQLiteRunStore(path) for _ in range(8)]

    await asyncio.gather(*(store.initialize() for store in stores))

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name = 'runs'"
            ).fetchone()[0]
            == 1
        )


@pytest.mark.parametrize("version", [2, 4])
async def test_unsupported_database_schema_is_rejected(
    tmp_path: Path,
    version: int,
) -> None:
    path = tmp_path / "runs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {version}")

    with pytest.raises(UnsupportedStorageVersionError, match="expected 3"):
        await SQLiteRunStore(path).initialize()


async def test_snapshot_change_does_not_require_a_metadata_event(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)

    result = await store.commit(
        RunCommit(
            state=state,
            events=[
                make_event(
                    run_id=state.identity.run_id,
                    type=RunEventType.RUN_STARTED,
                )
            ],
            expected_sequence=0,
            snapshot=SnapshotChange(
                snapshot_id=uuid4(),
                expected_version=0,
            ),
        )
    )

    assert await store.latest_sequence(state.identity.run_id) == 1
    assert await store.load_latest(state.identity.run_id) == result.snapshot


async def test_newer_record_versions_are_reported_as_unsupported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(path)
    await store.initialize()
    state = make_state()
    await store.create(state)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE runs SET schema_version = 99 WHERE run_id = ?",
            (str(state.identity.run_id),),
        )

    with pytest.raises(UnsupportedStorageVersionError, match="run"):
        await store.get(state.identity.run_id)


async def test_legacy_recovery_context_is_rejected_without_loading_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(path)
    await store.initialize()
    state = make_state()
    await store.create(state)
    legacy = (
        '{"schema_version":1,"workspace_id":"workspace-1",'
        '"workspace_spec":{"kind":"local","run_id":"'
        f"{state.identity.run_id}"
        '","source_dir":"/tmp/source","environment":'
        '{"API_TOKEN":"legacy-secret"}}}'
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE runs SET recovery_context_json = ? WHERE run_id = ?",
            (legacy, str(state.identity.run_id)),
        )

    with pytest.raises(UnsupportedStorageVersionError, match="recovery context"):
        await store.get(state.identity.run_id)


@pytest.mark.parametrize("record_kind", ["event", "snapshot", "approval"])
async def test_newer_related_record_versions_are_reported_as_unsupported(
    tmp_path: Path,
    record_kind: str,
) -> None:
    path = tmp_path / f"{record_kind}.sqlite3"
    store = SQLiteRunStore(path)
    await store.initialize()
    state = make_state()
    await store.create(state)

    if record_kind == "event":
        await store.append(
            make_event(run_id=state.identity.run_id, type=RunEventType.RUN_STARTED),
            expected_sequence=0,
        )
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute(
                "UPDATE run_events SET schema_version = 99 WHERE run_id = ?",
                (str(state.identity.run_id),),
            )
        operation = store.list_events(state.identity.run_id)
    elif record_kind == "snapshot":
        await store.save(
            NewRunSnapshot(
                run_id=state.identity.run_id,
                event_sequence=0,
                state=state,
            ),
            expected_version=0,
        )
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER run_snapshots_no_update")
            connection.execute(
                "UPDATE run_snapshots SET schema_version = 99 WHERE run_id = ?",
                (str(state.identity.run_id),),
            )
        operation = store.load_latest(state.identity.run_id)
    else:
        request, _ = await create_approval(
            store,
            state,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        with sqlite3.connect(path) as connection:
            connection.execute(
                "UPDATE approvals SET schema_version = 2 WHERE approval_id = ?",
                (str(request.approval_id),),
            )
        operation = store.get_approval(request.approval_id)

    with pytest.raises(UnsupportedStorageVersionError, match=record_kind):
        await operation


async def test_historical_snapshot_preserves_request(
    tmp_path: Path, runtime_persistence
) -> None:
    from miniagent.models.config import GenerationOptions
    from miniagent.models.messages import SystemMessage, UserMessage
    from miniagent.models.protocol import ModelProvider
    from miniagent.runtime import AgentRuntime
    from miniagent.tools.registry import ToolRegistry

    class UnusedProvider(ModelProvider):
        async def generate(self, request):
            raise AssertionError("no provider call expected")

    store = SQLiteRunStore(tmp_path / "history.sqlite3")
    await store.initialize()
    agent = AgentSpec(
        name="test",
        system_prompt="fixed",
        model=ModelTarget(provider="qwen", model="test"),
        generation=GenerationOptions(max_output_tokens=976),
    )
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=agent,
        config=RuntimeConfig(),
        messages=(SystemMessage(content="fixed"), UserMessage(content="task")),
    )
    await store.create(state)
    first = await store.save(
        NewRunSnapshot(run_id=state.identity.run_id, event_sequence=0, state=state),
        expected_version=0,
    )
    await store.save(
        NewRunSnapshot(run_id=state.identity.run_id, event_sequence=0, state=state),
        expected_version=1,
    )
    assert await store.load_version(state.identity.run_id, 1) == first
    assert await store.load_version(uuid4(), 1) is None
    assert await store.load_version(state.identity.run_id, 3) is None
    runtime = AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        run_store=runtime_persistence.store,
        provider=UnusedProvider(),
        tools=ToolRegistry([]),
    )
    restored = await store.load_version(state.identity.run_id, 1)
    assert restored is not None
    assert (
        runtime._build_request(restored.state).model_dump()
        == runtime._build_request(state).model_dump()
    )
