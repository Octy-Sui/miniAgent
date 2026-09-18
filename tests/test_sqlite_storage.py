import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.environments import LocalWorkspaceSpec
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
    PolicyEffect,
    PolicyRequest,
    RiskLevel,
)
from miniagent.runtime import (
    EventSequenceConflictError,
    EnvironmentReference,
    NewRunEvent,
    NewRunSnapshot,
    RunCursor,
    RunEventType,
    RunRecoveryContext,
    RunState,
    RunStatus,
    RuntimeConfig,
    SnapshotVersionConflictError,
    persist_workspace_spec,
    transition,
)
from miniagent.storage import (
    ApprovalConflictError,
    ApprovalExpiredError,
    CreateApproval,
    InvalidateApproval,
    ResolveApproval,
    RunCommit,
    SQLiteRunStore,
    SnapshotChange,
    UnsupportedStorageVersionError,
)


def make_state() -> RunState:
    now = datetime.now(UTC)
    return RunState(
        run_id=uuid4(),
        task="fix the task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="Test.",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=now,
        updated_at=now,
    )


def make_decision(state: RunState) -> PolicyDecision:
    digest = "a" * 64
    request = PolicyRequest(
        run_id=state.run_id,
        step_number=1,
        call_index=0,
        tool_call_id="call-1",
        tool_name="shell",
        arguments={"command": "pytest"},
        agent_spec_digest=digest,
        tool_contract_digest=digest,
        workspace_id="workspace-1",
        workspace_kind="local",
        execution_context_digest=digest,
    )
    return PolicyDecision(
        decision_id=uuid4(),
        request=request,
        request_digest=digest,
        policy_revision="policy-v1",
        effect=PolicyEffect.APPROVAL,
        risk=RiskLevel.HIGH,
        matched_rule_ids=("approval",),
        decisive_rule_ids=("approval",),
        reason_code="approval_required",
        reason="Approval is required.",
        input_summary="shell pytest",
        decided_at=datetime.now(UTC),
    )


async def create_approval(
    store: SQLiteRunStore,
    state: RunState,
    *,
    expires_at: datetime,
) -> tuple[ApprovalRequest, int]:
    decision = make_decision(state)
    request = ApprovalRequest(
        approval_id=uuid4(),
        decision_id=decision.decision_id,
        run_id=state.run_id,
        request_digest=decision.request_digest,
        policy_revision=decision.policy_revision,
        requested_at=expires_at - timedelta(minutes=5),
        expires_at=expires_at,
    )
    result = await store.commit(
        RunCommit(
            state=state,
            events=[
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_index=0,
                )
            ],
        )
    )
    return request, result.record.last_event_sequence


@pytest.fixture
async def store(tmp_path: Path) -> SQLiteRunStore:
    result = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await result.initialize()
    return result


async def test_event_sequences_are_contiguous_and_scoped_to_run(
    store: SQLiteRunStore,
) -> None:
    first = make_state()
    second = make_state()
    await store.create(first)
    await store.create(second)

    first_event = await store.append(
        NewRunEvent(run_id=first.run_id, type=RunEventType.RUN_STARTED),
        expected_sequence=0,
    )
    second_event = await store.append(
        NewRunEvent(run_id=second.run_id, type=RunEventType.RUN_STARTED),
        expected_sequence=0,
    )

    assert first_event.sequence == 1
    assert second_event.sequence == 1
    assert await store.latest_sequence(first.run_id) == 1


async def test_concurrent_append_with_same_expected_sequence_has_one_winner(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)
    events = [
        NewRunEvent(run_id=state.run_id, type=RunEventType.MODEL_REQUESTED),
        NewRunEvent(run_id=state.run_id, type=RunEventType.MODEL_RESPONDED),
    ]

    results = await asyncio.gather(
        *(store.append(event, expected_sequence=0) for event in events),
        return_exceptions=True,
    )

    assert len([result for result in results if not isinstance(result, Exception)]) == 1
    conflict = next(result for result in results if isinstance(result, Exception))
    assert isinstance(conflict, EventSequenceConflictError)
    assert [event.sequence for event in await store.list_events(state.run_id)] == [1]


async def test_commit_atomically_updates_state_context_events_and_snapshot(
    store: SQLiteRunStore,
    tmp_path: Path,
) -> None:
    state = make_state()
    await store.create(state)
    updated = state.model_copy(deep=True)
    transition(updated, RunStatus.PREPARING)
    context = RunRecoveryContext(
        workspace_id="workspace-1",
        workspace_spec=persist_workspace_spec(
            LocalWorkspaceSpec(
                run_id=state.run_id,
                source_dir=tmp_path,
            )
        ),
        policy_revision="policy-v1",
    )
    snapshot_id = uuid4()

    result = await store.commit(
        RunCommit(
            state=updated,
            events=[
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.STATE_TRANSITIONED,
                ),
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.SNAPSHOT_CREATED,
                    payload={"snapshot_id": str(snapshot_id), "version": 1},
                ),
            ],
            expected_sequence=0,
            recovery_context=context,
            snapshot=SnapshotChange(snapshot_id=snapshot_id, expected_version=0),
        )
    )

    assert [event.sequence for event in result.events] == [1, 2]
    assert result.record.state.status is RunStatus.PREPARING
    assert result.record.recovery_context == context
    assert result.record.last_event_sequence == 2
    assert result.snapshot is not None
    assert result.snapshot.event_sequence == 2
    assert result.snapshot.recovery_context == context
    assert await store.load_latest(state.run_id) == result.snapshot


async def test_snapshot_requires_current_semantic_state(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    state.completed_tool_call_ids = {"b", "a"}
    await store.create(state)
    equivalent = RunState.model_validate_json(state.model_dump_json())

    snapshot = await store.save(
        NewRunSnapshot(
            run_id=state.run_id,
            event_sequence=0,
            state=equivalent,
        ),
        expected_version=0,
    )

    assert snapshot.version == 1
    with pytest.raises(SnapshotVersionConflictError):
        await store.save(
            NewRunSnapshot(
                run_id=state.run_id,
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
        run_id=state.run_id,
        request_digest=decision.request_digest,
        policy_revision=decision.policy_revision,
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    waiting = state.model_copy(deep=True)
    transition(waiting, RunStatus.PREPARING)
    transition(waiting, RunStatus.RUNNING, cursor=RunCursor.REQUEST_MODEL)
    transition(
        waiting,
        RunStatus.WAITING_APPROVAL,
        pending_approval_id=str(request.approval_id),
    )
    created = await store.commit(
        RunCommit(
            state=waiting,
            events=[
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_index=0,
                )
            ],
        )
    )
    assert [item.request.approval_id for item in await store.list_pending_approvals()] == [
        request.approval_id
    ]
    resolution = ApprovalResolution(
        approval_id=request.approval_id,
        outcome=ApprovalOutcome.APPROVED,
        actor_kind="human",
        actor_id="operator",
        reason="Reviewed.",
        resolved_at=datetime.now(UTC),
    )
    resolved_state = waiting.model_copy(deep=True)
    resolved_state.approval_resolutions.append(resolution)
    change = RunCommit(
        state=resolved_state,
        events=[
            NewRunEvent(
                run_id=state.run_id,
                type=RunEventType.APPROVAL_RESOLVED,
                payload={"approval_id": str(request.approval_id)},
            )
        ],
        expected_sequence=created.record.last_event_sequence,
        approval_changes=[
            ResolveApproval(
                resolution=resolution,
                expected_version=1,
                resolution_event_index=0,
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
        run_id=state.run_id,
        request_digest=decision.request_digest,
        policy_revision=decision.policy_revision,
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    await store.commit(
        RunCommit(
            state=state,
            events=[
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.APPROVAL_REQUESTED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=0,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_index=0,
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
                    NewRunEvent(
                        run_id=state.run_id,
                        type=RunEventType.APPROVAL_RESOLVED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=1,
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=2,
                        resolution_event_index=0,
                    )
                ],
            )
        )

    assert await store.latest_sequence(state.run_id) == 1
    assert len(await store.list_events(state.run_id)) == 1


async def test_store_reopens_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    state = make_state()
    first = SQLiteRunStore(path)
    await first.initialize()
    await first.create(state)
    await first.append(
        NewRunEvent(run_id=state.run_id, type=RunEventType.RUN_STARTED),
        expected_sequence=0,
    )

    reopened = SQLiteRunStore(path)
    await reopened.initialize()

    assert (await reopened.get(state.run_id)).last_event_sequence == 1
    assert (await reopened.list_events(state.run_id))[0].type is RunEventType.RUN_STARTED


async def test_event_trace_scope_round_trips(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)

    stored = await store.append(
        NewRunEvent(
            run_id=state.run_id,
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
            NewRunEvent(
                run_id=other.run_id,
                type=RunEventType.APPROVAL_RESOLVED,
                payload={"approval_id": str(request.approval_id)},
            )
        ],
        expected_sequence=0,
        approval_changes=[
            ResolveApproval(
                resolution=resolution,
                expected_version=1,
                resolution_event_index=0,
            )
        ],
    )

    with pytest.raises(ApprovalConflictError, match="does not belong"):
        await store.commit(cross_run_resolution)

    resolved = await store.commit(
        RunCommit(
            state=owner,
            events=[
                NewRunEvent(
                    run_id=owner.run_id,
                    type=RunEventType.APPROVAL_RESOLVED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=owner_sequence,
            approval_changes=[
                ResolveApproval(
                    resolution=resolution,
                    expected_version=1,
                    resolution_event_index=0,
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
                    NewRunEvent(
                        run_id=other.run_id,
                        type=RunEventType.APPROVAL_INVALIDATED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=0,
                approval_changes=[
                    InvalidateApproval(
                        approval_id=request.approval_id,
                        expected_version=resolved.approvals[0].version,
                        invalidated_event_index=0,
                    )
                ],
            )
        )

    assert await store.latest_sequence(other.run_id) == 0
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
                    NewRunEvent(
                        run_id=state.run_id,
                        type=RunEventType.APPROVAL_RESOLVED,
                        payload={"approval_id": str(request.approval_id)},
                    )
                ],
                expected_sequence=sequence,
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=1,
                        resolution_event_index=0,
                    )
                ],
            )
        )

    assert await store.latest_sequence(state.run_id) == sequence
    assert (await store.get_approval(request.approval_id)).resolution is None


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
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.APPROVAL_RESOLVED,
                    payload={"approval_id": str(request.approval_id)},
                )
            ],
            expected_sequence=sequence,
            approval_changes=[
                ResolveApproval(
                    resolution=resolution,
                    expected_version=1,
                    resolution_event_index=0,
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
    assert retry.approvals[0].resolution == original


async def test_recovery_context_never_serializes_environment_values(
    store: SQLiteRunStore,
    tmp_path: Path,
) -> None:
    state = make_state()
    secret = "credential-value-that-must-not-be-stored"
    runtime_spec = LocalWorkspaceSpec(
        run_id=state.run_id,
        source_dir=tmp_path,
        environment={"API_TOKEN": secret},
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
                NewRunEvent(
                    run_id=state.run_id,
                    type=RunEventType.SNAPSHOT_CREATED,
                    payload={"snapshot_id": str(snapshot_id), "version": 1},
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
    event = NewRunEvent(
        run_id=state.run_id,
        type=RunEventType.MODEL_RESPONDED,
        payload={"value": "original"},
    )
    change = RunCommit(state=state, events=[event], expected_sequence=0)

    operation = store.commit(change)
    change.events.append(
        NewRunEvent(run_id=other.run_id, type=RunEventType.MODEL_REQUESTED)
    )
    change.events[0].payload["value"] = "mutated"
    result = await operation

    assert len(result.events) == 1
    assert result.events[0].payload == {"value": "original"}
    assert await store.latest_sequence(other.run_id) == 0


async def test_concurrent_initialization_serializes_migration(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    stores = [SQLiteRunStore(path) for _ in range(8)]

    await asyncio.gather(*(store.initialize() for store in stores))

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name = 'runs'"
        ).fetchone()[0] == 1


async def test_older_database_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "runs.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 1")

    with pytest.raises(UnsupportedStorageVersionError, match="expected 2"):
        await SQLiteRunStore(path).initialize()


async def test_snapshot_change_requires_matching_final_event(
    store: SQLiteRunStore,
) -> None:
    state = make_state()
    await store.create(state)

    with pytest.raises(ValueError, match="snapshot_created"):
        await store.commit(
            RunCommit(
                state=state,
                events=[
                    NewRunEvent(
                        run_id=state.run_id,
                        type=RunEventType.STATE_TRANSITIONED,
                    )
                ],
                expected_sequence=0,
                snapshot=SnapshotChange(
                    snapshot_id=uuid4(),
                    expected_version=0,
                ),
            )
        )

    assert await store.latest_sequence(state.run_id) == 0
    assert await store.load_latest(state.run_id) is None


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
            "UPDATE runs SET schema_version = 2 WHERE run_id = ?",
            (str(state.run_id),),
        )

    with pytest.raises(UnsupportedStorageVersionError, match="run"):
        await store.get(state.run_id)


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
        f"{state.run_id}"
        '","source_dir":"/tmp/source","environment":'
        '{"API_TOKEN":"legacy-secret"}}}'
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE runs SET recovery_context_json = ? WHERE run_id = ?",
            (legacy, str(state.run_id)),
        )

    with pytest.raises(UnsupportedStorageVersionError, match="recovery context"):
        await store.get(state.run_id)


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
            NewRunEvent(run_id=state.run_id, type=RunEventType.RUN_STARTED),
            expected_sequence=0,
        )
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute(
                "UPDATE run_events SET schema_version = 3 WHERE run_id = ?",
                (str(state.run_id),),
            )
        operation = store.list_events(state.run_id)
    elif record_kind == "snapshot":
        await store.save(
            NewRunSnapshot(
                run_id=state.run_id,
                event_sequence=0,
                state=state,
            ),
            expected_version=0,
        )
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER run_snapshots_no_update")
            connection.execute(
                "UPDATE run_snapshots SET schema_version = 2 WHERE run_id = ?",
                (str(state.run_id),),
            )
        operation = store.load_latest(state.run_id)
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
