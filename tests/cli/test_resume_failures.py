import pytest

from miniagent.cli.resume import ResumeConfig, resume_application
from miniagent.runtime import CancellationToken
from miniagent.runtime.events import RunEventType
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.resume import ResumeRejected
from miniagent.storage import SQLiteRunStore
from tests.resume_support import StoreBoundary


@pytest.mark.parametrize(
    ("event_type",), [(RunEventType.RESUME_STARTED,), (RunEventType.RESUME_COMPLETED,)]
)
@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_started_or_completed_uncertain_error_exits_without_more_work(
    resume_case, monkeypatch, event_type, action
):
    from miniagent.cli import resume as module

    application, _execution, provider = resume_case
    deps = application.dependencies
    boundary = StoreBoundary(deps.run_store, action=action, event_type=event_type)
    monkeypatch.setattr(
        module,
        "SQLiteRunStore",
        lambda path, **kwargs: (
            SQLiteRunStore(path, **kwargs) if kwargs.get("read_only") else boundary
        ),
    )
    with pytest.raises(RunPersistenceError) as raised:
        await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
    assert boundary.fired
    assert provider.requests == []
    attachment = deps.recovery_context.attachment
    assert attachment.repository_root.is_dir()
    events = await deps.run_store.list_events(deps.run_id)
    assert not any(event.type == RunEventType.RESUME_FAILED for event in events)
    assert not deps.artifact_store.root.exists()
    record = await deps.run_store.get(deps.run_id)
    applied = action == "applied_then_error"
    expected = (
        "running"
        if event_type == RunEventType.RESUME_COMPLETED and applied
        else "recovering"
        if event_type == RunEventType.RESUME_COMPLETED or applied
        else "running"
    )
    assert record.state.lifecycle.status.value == expected
    assert (
        raised.value.last_confirmed_sequence < record.last_event_sequence
        if applied
        else raised.value.last_confirmed_sequence == record.last_event_sequence
    )


async def test_missing_key_after_started_keeps_recovering_and_records_failure(
    resume_case, monkeypatch, tmp_path
):
    application, _execution, provider = resume_case
    deps = application.dependencies
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(ResumeRejected):
        await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
    record = await deps.run_store.get(deps.run_id)
    assert record.state.lifecycle.status.value == "recovering"
    assert record.state.recovery.session is not None
    assert provider.requests == []
    assert deps.recovery_context.attachment.repository_root.is_dir()
    assert (await deps.run_store.list_events(deps.run_id))[
        -1
    ].type == RunEventType.RESUME_FAILED


def _resume_until_started(database, run_id, channel):
    import asyncio

    from miniagent.cli import resume as module

    store = SQLiteRunStore(database)
    boundary = StoreBoundary(
        store,
        action="pause_after_commit",
        event_type=RunEventType.RESUME_STARTED,
        channel=channel,
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            module,
            "SQLiteRunStore",
            lambda path, **kwargs: (
                SQLiteRunStore(path, **kwargs) if kwargs.get("read_only") else boundary
            ),
        )
        asyncio.run(
            resume_application(
                ResumeConfig(run_id=run_id, database=database),
                cancellation_token=CancellationToken(),
            )
        )


async def test_killed_after_started_reenters_original_business_source(resume_case):
    import asyncio
    import multiprocessing

    application, _execution, _provider = resume_case
    deps = application.dependencies
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=_resume_until_started, args=(deps.run_store.path, deps.run_id, child)
    )
    process.start()
    try:
        await asyncio.wait_for(asyncio.to_thread(parent.recv), 5)
        process.kill()
        await asyncio.to_thread(process.join, 5)
        assert process.exitcode == -9
        record = await deps.run_store.get(deps.run_id)
        assert record.state.lifecycle.status.value == "recovering"
        assert record.state.recovery.session.business_status == "running"
        result = await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
        from miniagent.cli.application import ApplicationResult

        assert isinstance(result, ApplicationResult)
        assert result.run_result.status.value == "succeeded"
        assert (
            await deps.run_store.get(deps.run_id)
        ).state.recovery.resume_attempts == 2
    finally:
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
        parent.close()
        child.close()


@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_resume_failed_commit_uncertainty_stops_without_diagnostics(
    resume_case, monkeypatch, action, tmp_path
):
    from miniagent.cli import resume as module

    application, _execution, provider = resume_case
    deps = application.dependencies
    boundary = StoreBoundary(
        deps.run_store, action=action, event_type=RunEventType.RESUME_FAILED
    )
    monkeypatch.setattr(
        module,
        "SQLiteRunStore",
        lambda path, **kwargs: (
            SQLiteRunStore(path, **kwargs) if kwargs.get("read_only") else boundary
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(RunPersistenceError):
        await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
    assert boundary.fired and provider.requests == []
    assert deps.recovery_context.attachment.repository_root.is_dir()
    assert not deps.artifact_store.root.exists()


async def test_attachment_changes_after_started_retains_workspace_and_recovers_failure(
    resume_case, monkeypatch
):
    import asyncio
    from multiprocessing import Pipe

    from miniagent.cli import resume as module

    application, _execution, provider = resume_case
    deps = application.dependencies
    parent, child = Pipe()
    boundary = StoreBoundary(
        deps.run_store,
        action="pause_after_commit",
        event_type=RunEventType.RESUME_STARTED,
        channel=child,
    )
    monkeypatch.setattr(
        module,
        "SQLiteRunStore",
        lambda path, **kwargs: (
            SQLiteRunStore(path, **kwargs) if kwargs.get("read_only") else boundary
        ),
    )
    task = asyncio.create_task(
        resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
    )
    try:
        await asyncio.wait_for(asyncio.to_thread(parent.recv), 5)
        attachment = deps.recovery_context.attachment
        (attachment.workspace_dir / ".miniagent-workspace").write_text(
            "other workspace"
        )
        parent.send("continue")
        with pytest.raises(ResumeRejected, match="WORKSPACE_IDENTITY_MISMATCH"):
            await task
        record = await deps.run_store.get(deps.run_id)
        assert record.state.lifecycle.status.value == "recovering"
        assert attachment.repository_root.is_dir() and provider.requests == []
        assert (await deps.run_store.list_events(deps.run_id))[
            -1
        ].type == RunEventType.RESUME_FAILED
    finally:
        parent.close()
        child.close()
