import asyncio
import base64
import json
import socket
import struct
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from uuid import uuid4

import psutil
import pytest

from miniagent.environments.contracts import Command
from miniagent.environments.docker_connection import DockerConnection
from miniagent.environments.docker_exec import inspect_docker_execution, run_docker_exec
from miniagent.environments.docker_exec_helper import (
    MAX_FRAME_BYTES,
    Frame,
    HelperRequest,
    encode_frame,
    receive_frame,
)
from miniagent.environments.docker_supervisor import encode_guest_frame
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
from tests.resume_support import StoreBoundary, waiting_case


@pytest.fixture
def engine():
    class Engine:
        connection: DockerConnection

        def __init__(self):
            self.calls = []
            self.output_gate = threading.Event()
            self.running = False
            self.guest_completes = True
            self.delay_start = False
            self.start_received = threading.Event()
            self.start_gate = threading.Event()

    state = Engine()
    state.calls = []
    state.output_gate = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_POST(self):
            payload = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
            state.calls.append((self.path, monotonic(), payload))
            if self.path.endswith("/exec"):
                body = json.dumps({"Id": "exec-1"}).encode()
                self.send_response(201)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.endswith("/start"):
                state.start_received.set()
                if state.delay_start:
                    state.start_gate.wait(5)
                self.send_response(101)
                self.send_header("Connection", "Upgrade")
                self.send_header("Upgrade", "tcp")
                self.end_headers()
                self.wfile.flush()
                if state.output_gate.wait(5):
                    self.connection.settimeout(5)

                    def receive():
                        header = self.rfile.read(4)
                        if len(header) != 4:
                            raise EOFError
                        return json.loads(
                            self.rfile.read(struct.unpack(">I", header)[0])
                        )

                    def send(opening, kind, body):
                        frame = encode_guest_frame(
                            opening["attempt_id"], opening["execution_id"], kind, body
                        )
                        self.wfile.write(struct.pack(">BxxxI", 1, len(frame)) + frame)
                        self.wfile.flush()

                    try:
                        opening = receive()
                        while receive()["type"] != "configuration_end":
                            pass
                        send(
                            opening,
                            "guest_identified",
                            {
                                "guest": {
                                    "pid": 123,
                                    "pgid": 123,
                                    "sid": 123,
                                    "boot_id": "boot",
                                    "start_ticks": 1,
                                }
                            },
                        )
                        assert receive()["type"] == "permit_command"
                        send(
                            opening,
                            "data",
                            {
                                "stream": 1,
                                "content": base64.b64encode(b"helper-output").decode(),
                            },
                        )
                        if state.guest_completes:
                            send(
                                opening,
                                "completed",
                                {
                                    "status": "success",
                                    "exit_code": 0,
                                    "group_ended": True,
                                    "issuer_ended": True,
                                },
                            )
                    except (BrokenPipeError, ConnectionResetError, EOFError):
                        pass
                self.close_connection = True
            else:
                self.send_error(404)

        def do_GET(self):
            state.calls.append((self.path, monotonic(), None))
            body = json.dumps({"Running": state.running, "ExitCode": 0}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.connection = DockerConnection(
        None, f"tcp://127.0.0.1:{server.server_port}", None, False, "daemon-a"
    )
    yield state
    state.output_gate.set()
    state.start_gate.set()
    server.shutdown()
    server.server_close()
    thread.join(5)
    assert not thread.is_alive()


class Observer:
    def __init__(self, engine, fail=None):
        self.attempt_id = uuid4()
        self.engine = engine
        self.fail = fail
        self.facts = []

    async def identified(self, identity, *, permission="observe"):
        self.facts.append((identity, permission, monotonic()))
        if permission == self.fail:
            raise RunPersistenceError(
                run_id=uuid4(), operation="identity", last_confirmed_sequence=1
            )
        if isinstance(identity, DockerExecIdentity) and identity.started_observed:
            self.engine.output_gate.set()

    async def ended(self, evidence):
        self.facts.append((evidence, "ended", monotonic()))


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


PARENT_WORKER = """
import json, os, socket, subprocess, sys
from miniagent.environments.docker_exec_helper import Frame, encode_frame
opening = Frame.model_validate_json(sys.argv[1])
parent, child = socket.socketpair()
helper = subprocess.Popen([sys.executable, '-m', 'miniagent.environments.docker_exec_helper', str(child.fileno())], pass_fds=(child.fileno(),), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
child.close()
if sys.argv[2] == 'after_request':
    parent.sendall(encode_frame(opening))
print(helper.pid, flush=True)
sys.stdin.readline()
os._exit(0)
"""


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


async def test_expired_command_budget_issues_no_permission():
    observer = Observer(None)
    connection = DockerConnection(None, "tcp://127.0.0.1:1", None, False, "daemon-a")
    with pytest.raises(ManagedExecutionError, match="deadline"):
        await run_docker_exec(
            Command(argv=["true"]),
            connection=connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() - 1,
        )
    assert observer.facts == []


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
