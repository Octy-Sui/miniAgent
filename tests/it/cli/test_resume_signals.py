import asyncio
import signal

import pytest

from miniagent.cli.signals import run_with_signals
from miniagent.runtime import CancellationToken, InterruptionToken


@pytest.mark.parametrize(("sent",), [(signal.SIGINT,), (signal.SIGTERM,)])
async def test_first_signal_requests_interruption_not_terminal_cancellation(sent):
    import os

    ready = asyncio.Event()

    async def operation(
        cancellation: CancellationToken, interruption: InterruptionToken
    ):
        ready.set()
        await interruption.wait()
        assert not cancellation.is_cancelled
        return interruption.interruption.signal_number

    running = asyncio.create_task(run_with_signals(operation))
    await ready.wait()
    os.kill(os.getpid(), sent)
    value, received = await running
    assert value == sent and received == sent


async def test_repeated_cancellation_drains_sqlite_before_releasing_run_lock(
    resume_case,
):
    import sqlite3
    from multiprocessing import Pipe

    from miniagent.cli.execution_scope import application_execution_scope
    from miniagent.runtime.events import NewRunEvent, ProviderIdentityChanged
    from miniagent.runtime.recording import RunPersistenceError, RunRecorder
    from miniagent.storage.run_lock import RunInUseError, RunLock

    application, _execution, _provider = resume_case
    deps = application.dependencies
    before = await deps.run_store.get(deps.run_id)
    parent, child = Pipe()

    from tests.support.resume_helpers import StoreBoundary

    class DispatchStore(StoreBoundary):
        async def commit(self, change):
            child.send("dispatched")
            return await deps.run_store.commit(change)

    recorder = RunRecorder.attach(
        DispatchStore(deps.run_store, action="pause_after_commit"), before
    )
    blocker = sqlite3.connect(deps.run_store.path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")

    async def writing():
        async with application_execution_scope(deps.run_store.path, deps.run_id):
            return await recorder.commit(
                before.state,
                events=[
                    NewRunEvent(
                        run_id=deps.run_id,
                        payload=ProviderIdentityChanged(
                            original_endpoint_identity="https://original.test",
                            current_endpoint_identity="https://changed.test",
                        ),
                    )
                ],
            )

    task = asyncio.create_task(writing())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(parent.recv), 3) == "dispatched"
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        with pytest.raises(RunInUseError), RunLock(deps.run_store.path, deps.run_id):
            pass
    finally:
        blocker.rollback()
        blocker.close()
        parent.close()
        child.close()
    with pytest.raises(RunPersistenceError):
        await task
    after = await deps.run_store.get(deps.run_id)
    assert after.last_event_sequence == before.last_event_sequence + 1
    assert recorder.record == before
    with RunLock(deps.run_store.path, deps.run_id):
        assert await deps.run_store.get(deps.run_id) == after


async def test_expired_cleanup_deadline_cannot_start_a_sqlite_commit(resume_case):
    from time import monotonic

    from miniagent.environments.managed_execution import cleanup_deadline_scope
    from miniagent.runtime.events import NewRunEvent, ProviderIdentityChanged
    from miniagent.runtime.recording import RunPersistenceError, RunRecorder

    application, _execution, _provider = resume_case
    deps = application.dependencies
    before = await deps.run_store.get(deps.run_id)
    recorder = RunRecorder.attach(deps.run_store, before)
    with (
        cleanup_deadline_scope(lambda: monotonic() - 1),
        pytest.raises(RunPersistenceError),
    ):
        await recorder.commit(
            before.state,
            events=[
                NewRunEvent(
                    run_id=deps.run_id,
                    payload=ProviderIdentityChanged(
                        original_endpoint_identity="https://original.test",
                        current_endpoint_identity="https://changed.test",
                    ),
                )
            ],
        )
    assert await deps.run_store.get(deps.run_id) == before
