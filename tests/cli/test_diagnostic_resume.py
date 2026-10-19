from time import monotonic

import pytest

from miniagent.cli.diagnostic_finalization import finalize_diagnostic
from miniagent.runtime import RunStatus, TerminationReason
from miniagent.runtime.execution import RunTermination
from miniagent.runtime.recording import RunRecorder


@pytest.mark.parametrize(
    ("status",), [(RunStatus.FAILED,), (RunStatus.CANCELLED,), (RunStatus.SUSPENDED,)]
)
async def test_diagnostic_only_appends_events(resume_case, status):
    application, execution, _provider = resume_case
    deps = application.dependencies
    if status is RunStatus.SUSPENDED:
        from miniagent.runtime import InterruptionToken

        token = InterruptionToken()
        token.request(2)
        await execution.suspend(token.interruption, deadline=token.deadline)
    else:
        await execution.finish(
            RunTermination(
                status=status,
                reason=TerminationReason.MODEL_FAILED
                if status is RunStatus.FAILED
                else TerminationReason.CANCEL_REQUESTED,
            )
        )
    attachment = deps.recovery_context.attachment
    (attachment.repository_root / "source.txt").write_text("diagnostic change\n")
    before = await deps.run_store.get(deps.run_id)
    recorder = RunRecorder.attach(deps.run_store, before)
    result = await finalize_diagnostic(
        recorder=recorder,
        workspace=deps.workspace,
        artifact_store=deps.artifact_store,
        deadline=monotonic() + 10,
    )
    assert result.status == "submitted"
    after = await deps.run_store.get(deps.run_id)
    assert after.state == before.state
    assert after.latest_snapshot_version == before.latest_snapshot_version
    assert after.last_event_sequence > before.last_event_sequence
    events = await deps.run_store.list_events(
        deps.run_id, after_sequence=before.last_event_sequence
    )
    assert events[0].type.value == "diagnostic_finalization_started"
    assert events[-1].type.value == "diagnostic_finalization_completed"
    assert after.state.execution.evaluation == before.state.execution.evaluation
    assert (await deps.artifact_store.get_record(result.patch_ref)).metadata[
        "kind"
    ] == "diagnostic"


@pytest.mark.parametrize(
    ("event_type",),
    [
        ("diagnostic_finalization_started",),
        ("diagnostic_finalization_execution_identified",),
        ("diagnostic_finalization_execution_ended",),
        ("diagnostic_finalization_completed",),
    ],
)
@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_terminal_diagnostic_crash_never_opens_business_cursor(
    resume_case, event_type, action
):
    from miniagent.runtime.events import RunEventType
    from miniagent.runtime.recording import RunPersistenceError
    from tests.resume_support import StoreBoundary

    application, execution, _provider = resume_case
    deps = application.dependencies
    await execution.finish(
        RunTermination(status=RunStatus.FAILED, reason=TerminationReason.MODEL_FAILED)
    )
    attachment = deps.recovery_context.attachment
    (attachment.repository_root / "source.txt").write_text("changed\n")
    before = await deps.run_store.get(deps.run_id)
    boundary = StoreBoundary(
        deps.run_store, action=action, event_type=RunEventType(event_type)
    )
    recorder = RunRecorder.attach(boundary, before)
    with pytest.raises(RunPersistenceError):
        await finalize_diagnostic(
            recorder=recorder,
            workspace=deps.workspace,
            artifact_store=deps.artifact_store,
            deadline=monotonic() + 10,
        )
    assert boundary.fired
    after = await deps.run_store.get(deps.run_id)
    assert (
        after.state == before.state
        and after.latest_snapshot_version == before.latest_snapshot_version
    )
    assert after.state.execution.active_operation is None
    events = await deps.run_store.list_events(
        deps.run_id, after_sequence=before.last_event_sequence
    )
    assert all(
        event.type.value.startswith("diagnostic_finalization_") for event in events
    )
    assert all(event.sequence <= after.last_event_sequence for event in events)


async def test_uncertain_diagnostic_blocks_suspended_resume(resume_case):
    from miniagent.cli.resume import ResumeConfig, resume_application
    from miniagent.runtime import CancellationToken, InterruptionToken
    from miniagent.runtime.events import RunEventType
    from miniagent.runtime.recording import RunPersistenceError
    from miniagent.runtime.resume import ResumeRejected
    from tests.resume_support import StoreBoundary

    application, execution, provider = resume_case
    deps = application.dependencies
    token = InterruptionToken()
    token.request(2)
    await execution.suspend(token.interruption, deadline=token.deadline)
    record = await deps.run_store.get(deps.run_id)
    boundary = StoreBoundary(
        deps.run_store,
        action="applied_then_error",
        event_type=RunEventType.DIAGNOSTIC_FINALIZATION_STARTED,
    )
    with pytest.raises(RunPersistenceError):
        await finalize_diagnostic(
            recorder=RunRecorder.attach(boundary, record),
            workspace=deps.workspace,
            artifact_store=deps.artifact_store,
            deadline=monotonic() + 10,
        )
    with pytest.raises(ResumeRejected, match="WORKSPACE_BUSY"):
        await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
    assert provider.requests == []
    assert deps.recovery_context.attachment.repository_root.is_dir()


async def test_direct_diagnostic_cannot_use_business_observer(resume_case):
    from miniagent.runtime.execution import evaluation_scope

    application, execution, _provider = resume_case
    before = execution.state
    with evaluation_scope(execution.evaluation_observer()):
        result = await application.submission.submit(kind="diagnostic")
    assert result.status == "empty"
    assert execution.state == before


async def test_resume_closes_verified_old_diagnostic_before_business_completion(
    resume_case,
):
    from miniagent.cli.resume import ResumeConfig, resume_application
    from miniagent.runtime import CancellationToken, InterruptionToken
    from miniagent.runtime.events import RunEventType
    from miniagent.runtime.recording import RunPersistenceError
    from tests.resume_support import StoreBoundary

    application, execution, _provider = resume_case
    deps = application.dependencies
    token = InterruptionToken()
    token.request(2)
    await execution.suspend(token.interruption, deadline=token.deadline)
    before = await deps.run_store.get(deps.run_id)
    boundary = StoreBoundary(
        deps.run_store,
        action="not_dispatched_then_error",
        event_type=RunEventType.DIAGNOSTIC_FINALIZATION_EXECUTION_ENDED,
    )
    with pytest.raises(RunPersistenceError):
        await finalize_diagnostic(
            recorder=RunRecorder.attach(boundary, before),
            workspace=deps.workspace,
            artifact_store=deps.artifact_store,
            deadline=monotonic() + 10,
        )
    await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
        cancellation_token=CancellationToken(),
    )
    events = await deps.run_store.list_events(
        deps.run_id, after_sequence=before.last_event_sequence
    )
    closed = next(
        event
        for event in events
        if event.type == RunEventType.DIAGNOSTIC_FINALIZATION_INTERRUPTED
        and event.payload.execution_ended
    )
    completed = next(
        event for event in events if event.type == RunEventType.RESUME_COMPLETED
    )
    assert closed.sequence < completed.sequence
