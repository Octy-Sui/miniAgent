import asyncio
import socket
import struct
import subprocess
import sys
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

import psutil
import pytest

from miniagent.environments.contracts import Command
from miniagent.environments.docker_exec import inspect_docker_execution, run_docker_exec
from miniagent.environments.docker_exec_helper import (
    MAX_FRAME_BYTES,
    Frame,
    HelperRequest,
    encode_frame,
    receive_frame,
)
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    ManagedExecutionError,
)
from miniagent.environments.process import (
    identify_host_process,
    inspect_host_execution,
)
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime.events import OperationExecutionIdentified, RunEventType
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from tests.support.environments.docker_exec_helper import PARENT_WORKER, Observer
from tests.support.resume_helpers import StoreBoundary, waiting_case


async def test_exec_rpcs_require_confirmed_identity_permissions(engine):
    """TC-U11-01/03: real helper sends each mutating RPC only after observer ack."""
    observer = Observer(engine)
    result = await run_docker_exec(
        Command(argv=["printf", "helper-output"]),
        connection=engine.connection,
        container_id="a" * 64,
        observer=observer,
        attempt_id=observer.attempt_id,
        deadline=monotonic() + 5,
    )
    assert result.stdout == "helper-output" and result.exit_code == 0
    create_at = next(
        time for _, permission, time in observer.facts if permission == "create_exec"
    )
    start_at = next(
        time for _, permission, time in observer.facts if permission == "start_exec"
    )
    create_rpc = [time for path, time, _ in engine.calls if path.endswith("/exec")]
    start_rpc = [time for path, time, _ in engine.calls if path.endswith("/start")]
    assert len(create_rpc) == len(start_rpc) == 1
    assert create_at < create_rpc[0] < start_at < start_rpc[0]


@pytest.mark.parametrize(
    ("permission", "expected_rpc_suffixes"),
    [("create_exec", []), ("start_exec", ["/exec"])],
)
async def test_uncertain_identity_commit_never_releases_permission(
    engine, permission, expected_rpc_suffixes
):
    """TC-U11-04/05: caller must stop after identity/start intent commit uncertainty."""
    observer = Observer(engine, permission)
    with pytest.raises(RunPersistenceError):
        await run_docker_exec(
            Command(argv=["true"]),
            connection=engine.connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    assert [
        "/" + path.rsplit("/", 1)[-1] for path, _, _ in engine.calls
    ] == expected_rpc_suffixes


async def test_start_permission_without_started_proof_is_unknown(engine):
    """TC-U11-06/07: helper exit plus Running=false cannot rule out delayed start."""
    identity = DockerExecIdentity(
        attempt_id=uuid4(),
        execution_id=uuid4(),
        helper_execution_id=uuid4(),
        daemon_id="daemon-a",
        container_id="a" * 64,
        exec_id="exec-1",
        start_permitted=True,
        started_observed=False,
    )
    evidence = await inspect_docker_execution(identity, connection=engine.connection)
    assert evidence.outcome == "unknown"
    assert not evidence.group_ended
    assert all(not path.endswith("/start") for path, _, _ in engine.calls)


@pytest.mark.parametrize(
    ("condition",),
    [
        ("before_request_eof",),
        ("permission_eof",),
        ("permission_timeout",),
        ("wrong_attempt",),
        ("oversized",),
        ("start_eof",),
        ("start_timeout",),
        ("start_wrong_attempt",),
    ],
)
def test_helper_without_valid_create_permission_sends_no_rpc(engine, condition):
    """TC-U11-02/09..12: real helper exits at EOF, deadline or invalid gate."""

    parent, child = socket.socketpair()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "miniagent.environments.docker_exec_helper",
            str(child.fileno()),
        ],
        pass_fds=(child.fileno(),),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child.close()
    try:
        attempt_id, execution_id = uuid4(), uuid4()
        request = HelperRequest(
            mode="execute",
            endpoint=engine.connection.endpoint,
            cert_path=None,
            tls_verify=False,
            container_id="a" * 64,
            command=Command(argv=["true"]),
            deadline=monotonic() + 1.0,
        )
        if condition == "oversized":
            parent.sendall(struct.pack(">I", MAX_FRAME_BYTES + 1))
        elif condition != "before_request_eof":
            parent.sendall(
                encode_frame(
                    Frame(
                        attempt_id=attempt_id,
                        execution_id=execution_id,
                        type="request",
                        body=request.model_dump(mode="json"),
                    )
                )
            )
        if condition.startswith("start_"):
            parent.sendall(
                encode_frame(
                    Frame(
                        attempt_id=attempt_id,
                        execution_id=execution_id,
                        type="permit_create",
                    )
                )
            )
            assert receive_frame(parent, request.deadline).type == "exec_created"
            if condition == "start_eof":
                parent.close()
            elif condition == "start_wrong_attempt":
                parent.sendall(
                    encode_frame(
                        Frame(
                            attempt_id=uuid4(),
                            execution_id=execution_id,
                            type="permit_start",
                        )
                    )
                )
        elif condition in ("before_request_eof", "permission_eof", "oversized"):
            parent.close()
        elif condition == "wrong_attempt":
            parent.sendall(
                encode_frame(
                    Frame(
                        attempt_id=uuid4(),
                        execution_id=execution_id,
                        type="permit_create",
                    )
                )
            )
        assert process.wait(timeout=5) != 0
        assert len(engine.calls) == (1 if condition.startswith("start_") else 0)
        assert not any(path.endswith("/start") for path, _, _ in engine.calls)
    finally:
        parent.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize(
    ("running", "started", "expected"),
    [
        (False, False, "unknown"),
        (False, True, "unknown"),
        (True, False, "active"),
        (True, True, "active"),
    ],
)
async def test_inspection_never_claims_group_end_from_exec_state(
    engine, running, started, expected
):
    """TC-U11-06/07/08: exec state alone never certifies the guest group."""
    engine.running = running
    identity = DockerExecIdentity(
        attempt_id=uuid4(),
        execution_id=uuid4(),
        helper_execution_id=uuid4(),
        daemon_id="daemon-a",
        container_id="a" * 64,
        exec_id="exec-1",
        start_permitted=True,
        started_observed=started,
    )
    evidence = await inspect_docker_execution(identity, connection=engine.connection)
    assert evidence.outcome == expected and not evidence.group_ended
    assert all(path.endswith("/json") for path, _, _ in engine.calls)


@pytest.mark.parametrize(("permission",), [("create_exec",), ("start_exec",)])
@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_identity_commit_applied_and_unapplied_both_stop_live_helper(
    tmp_path, engine, permission, action
):
    """F2 / TC-U11-04/05: real SQLite evidence separates identical caller errors."""

    store, execution, request = await waiting_case(tmp_path / "runs.sqlite3")
    await execution.resolve_approval(
        ApprovalResolution(
            approval_id=request.approval_id,
            outcome=ApprovalOutcome.APPROVED,
            actor_kind="human",
            actor_id="tester",
            reason="approved",
            resolved_at=datetime.now(UTC),
        )
    )
    await execution.begin_tool_execution()
    record = await store.get(request.run_id)
    boundary = StoreBoundary(
        store,
        action=action,
        event_type=RunEventType.OPERATION_EXECUTION_IDENTIFIED,
        permission=permission,
    )
    execution.journal = RunJournal.attach(
        RunRecorder.attach(boundary, record),
        [await store.get_approval(request.approval_id)],
    )
    operation = execution.state.execution.active_operation
    assert operation is not None
    observer = execution.execution_observer(operation.attempt_id)
    with pytest.raises(RunPersistenceError):
        await run_docker_exec(
            Command(argv=["true"]),
            connection=engine.connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    assert boundary.fired
    paths = [path for path, _, _ in engine.calls]
    assert len(paths) == (0 if permission == "create_exec" else 1)
    assert not any(path.endswith("/start") for path in paths)
    facts = [
        event
        for event in await store.list_events(request.run_id)
        if event.type is RunEventType.OPERATION_EXECUTION_IDENTIFIED
        and isinstance(event.payload, OperationExecutionIdentified)
        and event.payload.permission == permission
    ]
    assert len(facts) == (1 if action == "applied_then_error" else 0)


async def test_permitted_delayed_start_stays_unknown_after_helper_exit(engine):
    """TC-U11-06/07: a live Engine start request can outlast the issuing helper."""
    engine.delay_start = True
    observer = Observer(engine)
    task = asyncio.create_task(
        run_docker_exec(
            Command(argv=["true"]),
            connection=engine.connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    )
    try:
        assert await asyncio.to_thread(engine.start_received.wait, 2), (
            "start RPC barrier was not reached"
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        remote = next(
            identity
            for identity, _, _ in observer.facts
            if isinstance(identity, DockerExecIdentity)
        )
        assert remote.start_permitted and not remote.started_observed
        assert not engine.start_gate.is_set()
        assert (
            await inspect_docker_execution(remote, connection=engine.connection)
        ).outcome == "unknown"
        assert sum(path.endswith("/start") for path, _, _ in engine.calls) == 1
    finally:
        engine.start_gate.set()
        engine.output_gate.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(("window",), [("before_request",), ("after_request",)])
async def test_helper_parent_death_before_permission_sends_no_rpc(engine, window):
    """TC-U11-02 / G4: a real killed issuer closes the sole permission channel."""

    attempt_id, execution_id = uuid4(), uuid4()
    request = HelperRequest(
        mode="execute",
        endpoint=engine.connection.endpoint,
        cert_path=None,
        tls_verify=False,
        container_id="a" * 64,
        command=Command(argv=["true"]),
        deadline=monotonic() + 5,
    )
    opening = Frame(
        attempt_id=attempt_id,
        execution_id=execution_id,
        type="request",
        body=request.model_dump(mode="json"),
    )
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        PARENT_WORKER,
        opening.model_dump_json(),
        window,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    identity = None
    try:
        assert parent.stdout is not None
        helper_pid = int(await asyncio.wait_for(parent.stdout.readline(), 3))
        identity = identify_host_process(
            helper_pid,
            attempt_id=attempt_id,
            execution_id=execution_id,
            role="docker_helper",
        )
        parent.kill()
        await asyncio.wait_for(parent.wait(), 5)
        async with asyncio.timeout(5):
            while inspect_host_execution(identity).outcome != "ended":
                await asyncio.sleep(0.01)
        assert engine.calls == []
    finally:
        if parent.returncode is None:
            parent.kill()
        await asyncio.wait_for(parent.wait(), 5)
        if parent.stdin is not None:
            parent.stdin.close()
        if identity is not None:
            try:
                process = psutil.Process(identity.pid)
                if (
                    process.create_time() == identity.create_time
                    and process.status() != psutil.STATUS_ZOMBIE
                ):
                    process.kill()
            except psutil.NoSuchProcess:
                pass


async def test_helper_output_limit_is_preserved(engine):
    observer = Observer(engine)
    result = await run_docker_exec(
        Command(argv=["printf", "helper-output"]),
        connection=engine.connection,
        container_id="a" * 64,
        observer=observer,
        attempt_id=observer.attempt_id,
        deadline=monotonic() + 5,
        max_output_bytes=3,
    )
    assert result.stdout == "hel" and result.stdout_truncated


async def test_docker_exec_completes_only_after_helper_exec_and_group_end(engine):
    engine.guest_completes = False
    observer = Observer(engine)
    with pytest.raises(ManagedExecutionError, match="complete guest evidence"):
        await run_docker_exec(
            Command(argv=["true"]),
            connection=engine.connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    assert not any(
        isinstance(fact, DockerExecIdentity) and permission == "ended"
        for fact, permission, _ in observer.facts
    )


async def test_interruption_between_start_commit_and_permission_sends_no_start_rpc(
    engine,
):
    from miniagent.runtime.interruption import InterruptionRequested

    class StopAfterCommit(Observer):
        def check_permission(self):
            if any(permission == "start_exec" for _, permission, _ in self.facts):
                raise InterruptionRequested

    observer = StopAfterCommit(engine)
    with pytest.raises(InterruptionRequested):
        await run_docker_exec(
            Command(argv=["printf", "must-not-start"]),
            connection=engine.connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() + 5,
        )
    assert not any(path.endswith("/start") for path, _time, _body in engine.calls)
