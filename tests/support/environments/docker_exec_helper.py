import base64
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.environments.docker_connection import DockerConnection
from miniagent.environments.docker_supervisor import encode_guest_frame
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
)
from miniagent.runtime.recording import RunPersistenceError


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
    def check_permission(self):
        pass

    def __init__(self, engine, fail=None):
        self.attempt_id = uuid4()
        self.engine = engine
        self.fail = fail
        self.facts = []

    async def identified(self, identity, *, permission="observe", command=None):
        self.facts.append((identity, permission, monotonic()))
        if permission == self.fail:
            raise RunPersistenceError(
                run_id=uuid4(), operation="identity", last_confirmed_sequence=1
            )
        if isinstance(identity, DockerExecIdentity) and identity.started_observed:
            self.engine.output_gate.set()

    async def output_recorded(self, output):
        self.facts.append((output, "output", monotonic()))

    async def ended(self, evidence):
        self.facts.append((evidence, "ended", monotonic()))


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
