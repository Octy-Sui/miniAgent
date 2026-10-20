import base64
import hashlib
import json
import os
import select
import signal
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    DockerOptions,
    DockerWorkspace,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
)
from miniagent.environments.docker_connection import DockerConnection
from miniagent.environments.docker_supervisor import encode_guest_frame


@pytest.fixture
def export_workspace(tmp_path, export_engine):
    executable = tmp_path / "docker-export"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
with (root / "export-calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if "rm" in args:
    (root / "cleanup-started").write_text(str(os.getpid()))
    mode = (root / "mode").read_text()
    if mode == "hang":
        time.sleep(30)
    elif mode == "error":
        sys.exit(1)
elif "diff" in args:
    if "--name-only" in args:
        sys.stdout.buffer.write(b"changed.txt\\0")
    else:
        sys.stdout.write(" " if (root / "mode").read_text() == "whitespace" else "diff --git a/changed.txt b/changed.txt\\n")
        if (root / "mode").read_text() == "large":
            sys.stdout.write("x" * (16 * 1024 * 1024))
"""
    )
    executable.chmod(0o755)
    (tmp_path / "mode").write_text("ok")
    backend = DockerWorkspaceBackend(
        artifact_store=FileArtifactStore(tmp_path / "unused-artifacts"),
        docker_executable=str(executable),
        connection=export_engine(executable),
    )
    return DockerWorkspace(
        backend=backend,
        spec=DockerWorkspaceSpec(
            run_id=uuid4(), options=DockerOptions(image="synthetic", workdir="/testbed")
        ),
        _workspace_id="test",
        container_id="a" * 64,
        image_id="image",
        base_revision="actual-base",
    )


@pytest.fixture
def prepared_bundle(tmp_path: Path) -> Path:
    support = json.loads(
        files("miniagent.evaluation")
        .joinpath("data/swebench-verified-25.json")
        .read_bytes()
    )
    root = tmp_path / "prepared"
    root.mkdir()
    records = [
        {
            "instance_id": task["instance_id"],
            "repo": task["repo"],
            "version": task["version"],
            "base_commit": task["base_commit"],
            "image": task["image"],
            "eval_script": "echo synthetic-tests\n",
            "log_parser": "synthetic",
            "eval_type": "pass_and_fail",
            "FAIL_TO_PASS": ["f"],
            "PASS_TO_PASS": ["p"],
        }
        for task in support["tasks"]
    ]
    content = json.dumps(records).encode()
    (root / "tasks.json").write_bytes(content)
    instance = "django__django-11477"
    report = json.dumps(
        {
            instance: {
                "patch_exists": True,
                "patch_is_None": False,
                "patch_successfully_applied": True,
                "resolved": True,
                "infra_failure": False,
                "tests_status": {
                    "FAIL_TO_PASS": {"success": ["f"], "failure": []},
                    "PASS_TO_PASS": {"success": ["p"], "failure": []},
                },
            }
        }
    ).encode()
    (root / "preflight.json").write_bytes(report)
    manifest = {
        "ready": False,
        "harness_version": "5.0.2",
        "task_base_sha256": support["task_base_sha256"],
        "sources": support["sources"],
        "validation": {
            "task_count": 25,
            "make_test_spec": "25/25 passed",
            "source_digests": "verified",
        },
        "outputs": {
            "tasks.json": {"sha256": hashlib.sha256(content).hexdigest(), "count": 25}
        },
        "tasks": [
            {
                "instance_id": task["instance_id"],
                "selection": task["selection"],
                "image": task["image"],
                "gold_preflight": (
                    {
                        "status": "passed",
                        "resolved": True,
                        "report": "preflight.json",
                        "report_sha256": hashlib.sha256(report).hexdigest(),
                    }
                    if task["instance_id"] == instance
                    else {"status": "not_run", "report": None}
                ),
            }
            for task in support["tasks"]
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


@pytest.fixture
def docker_cli(tmp_path: Path) -> Path:
    executable = tmp_path / "docker-test"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
scenario = json.loads((root / "docker-scenario.json").read_text())
args = sys.argv[1:]
with (root / "docker-calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"argv": args, "environment": {key: value for key, value in os.environ.items() if key.startswith("DOCKER_")}}) + "\\n")
if scenario.get("hang"):
    (root / "docker-started").write_text(str(os.getpid()))
    time.sleep(30)
if args[:2] == ["context", "show"]:
    print(scenario.get("current", "a"))
elif args[:2] == ["context", "inspect"]:
    print(json.dumps([scenario["contexts"][args[2]]]))
elif "info" in args:
    print(scenario.get("daemon_id", "daemon-a"))
elif "image" in args and "inspect" in args:
    if scenario.get("image_missing"):
        print("Error response from daemon: No such image: test", file=sys.stderr)
        sys.exit(1)
    print(scenario.get("image_id", "sha256:synthetic"))
elif "container" in args and "create" in args:
    scenario["containers"] = scenario.get("containers", []) + [{"Id": scenario.get("container_id", "agent-container"), "Name": "/miniagent-agent", "Image": "sha256:agent"}]
    (root / "docker-scenario.json").write_text(json.dumps(scenario))
    print(scenario.get("container_id", "agent-container"))
elif "container" in args and "start" in args:
    pass
elif "container" in args and "exec" in args:
    if "rev-parse" in args:
        print("a" * 40)
    elif "diff" in args:
        if "--name-only" in args:
            sys.stdout.buffer.write(b"changed.txt\\0")
        else:
            print("diff --git a/changed.txt b/changed.txt")
elif "container" in args and "ls" in args:
    for item in scenario.get("containers", []):
        print(json.dumps({"ID": item["Id"], "Names": item["Name"].lstrip("/")}))
elif "container" in args and "inspect" in args:
    identity = args[-1]
    print(json.dumps([item for item in scenario.get("containers", []) if item["Id"] == identity]))
elif "container" in args and "rm" in args:
    if scenario.get("delete_fail"):
        print("delete failed", file=sys.stderr)
        sys.exit(1)
    scenario["containers"] = [item for item in scenario.get("containers", []) if item["Id"] != args[-1]]
    (root / "docker-scenario.json").write_text(json.dumps(scenario))
else:
    sys.exit(2)
"""
    )
    executable.chmod(0o755)
    (tmp_path / "docker-scenario.json").write_text(
        json.dumps(
            {
                "contexts": {
                    "a": {
                        "Endpoints": {
                            "docker": {
                                "Host": "unix:///private/tmp/daemon-a.sock",
                                "SkipTLSVerify": False,
                            }
                        },
                        "TLSMaterial": {},
                        "Storage": {"TLSPath": ""},
                    }
                }
            }
        )
    )
    return executable


@pytest.fixture
def harness_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "harness-test"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, signal, subprocess, sys, time
from pathlib import Path
root = Path(__file__).parent
scenario = json.loads((root / "harness-scenario.json").read_text())
args = sys.argv[1:]
if args == ["--version"]:
    print("swebench 5.0.2")
    sys.exit(0)
cwd = Path.cwd()
(root / "harness-call.json").write_text(json.dumps({"argv": args, "cwd": str(cwd), "host": os.environ.get("DOCKER_HOST")}))
task = json.loads(Path(args[1]).read_bytes())[0]
prediction = json.loads(Path(args[args.index("--predictions") + 1]).read_bytes())
run = args[args.index("--run-id") + 1]
instance = task["instance_id"]
directory = cwd / "logs/run_evaluation" / run / prediction["model_name_or_path"] / instance
directory.mkdir(parents=True)
patch = prediction["model_patch"].encode()
(directory / "patch.diff").write_bytes(patch if scenario.get("mode") != "wrong_patch" else b"wrong")
(directory / "test_output.txt").write_text("synthetic tests")
(directory / "run_instance.log").write_text("synthetic log")
report = {instance: {"patch_exists": True, "patch_is_None": False, "patch_successfully_applied": True,
    "resolved": True, "infra_failure": False, "tests_status": {
        "FAIL_TO_PASS": {"success": task["FAIL_TO_PASS"], "failure": []},
        "PASS_TO_PASS": {"success": task["PASS_TO_PASS"], "failure": []}}}}
mode = scenario.get("mode", "pass")
if mode == "unresolved":
    report[instance]["resolved"] = False
    report[instance]["tests_status"]["FAIL_TO_PASS"] = {"success": [], "failure": task["FAIL_TO_PASS"]}
if mode == "wrong_task":
    report = {"other": report[instance]}
if mode == "string_bool":
    report[instance]["resolved"] = "true"
if mode == "missing_tests":
    report[instance]["tests_status"]["FAIL_TO_PASS"]["success"] = []
if mode not in ("missing_report", "test_timeout", "fake_timeout"):
    (directory / "report.json").write_bytes(b"bad json" if mode == "bad_report" else json.dumps(report).encode())
if mode == "test_timeout":
    timeout = args[args.index("--timeout") + 1]
    (directory / "test_output.txt").write_bytes(f"\\n\\nTimeout error: {timeout} seconds exceeded.".encode())
if mode == "external_report":
    outside = root / "outside-report.json"
    outside.write_text(json.dumps(report))
    (directory / "report.json").unlink()
    (directory / "report.json").symlink_to(outside)
if mode == "fake_timeout":
    print("timeout occurred somewhere")
if scenario.get("omit"):
    (directory / scenario["omit"]).unlink(missing_ok=True)
docker_path = root / "docker-scenario.json"
docker = json.loads(docker_path.read_text())
name = f"sweb.eval.{instance.lower()}.{run}"
docker["containers"] = docker.get("containers", []) + [
    {"Id": "a" * 64, "Name": "/" + name, "Image": "sha256:actual"},
    {"Id": "b" * 64, "Name": "/" + name + ".12345", "Image": "sha256:actual"},
    {"Id": "neighbor", "Name": "/" + name + ".not-digits", "Image": "sha256:neighbor"}]
if scenario.get("daemon_change"):
    docker["daemon_id"] = "daemon-b"
if scenario.get("delete_fail"):
    docker["delete_fail"] = True
docker_path.write_text(json.dumps(docker))
print("harness stdout" + "x" * scenario.get("log_size", 0))
print("harness stderr", file=sys.stderr)
if scenario.get("block_store"):
    path = Path(scenario["block_store"]) / "manifests" / prediction.get("unused", "blocked")
    # The root becomes a file after inputs were read, so real publication fails.
    content_root = Path(scenario["block_store"]) / "content"
    content_root.rename(Path(scenario["block_store"]) / "saved-content")
    content_root.write_text("blocked")
if scenario.get("hang"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    (root / "harness-child").write_text(str(child.pid))
    if scenario.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / "harness-started").write_text(str(os.getpid()))
    time.sleep(30)
sys.exit(scenario.get("exit_code", 0))
"""
    )
    executable.chmod(0o755)
    (tmp_path / "harness-scenario.json").write_text("{}")
    return executable


@pytest.fixture
async def evaluation_case(tmp_path, prepared_bundle, docker_cli, harness_executable):
    from miniagent.artifacts import ArtifactWrite
    from miniagent.cli.settings import Settings
    from miniagent.evaluation import CandidateOutput, EvaluationRequest
    from miniagent.evaluation.swebench import SWEbenchEvaluator
    from miniagent.evaluation.swebench_data import (
        SWEbenchTaskContext,
        load_prepared_task,
    )
    from miniagent.evaluation.swebench_docker import resolve_docker_connection

    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    patch = await store.put(
        ArtifactWrite(
            run_id=run_id,
            type="workspace_patch",
            metadata={"submission_id": str(uuid4()), "base_revision": "agent-base"},
        ),
        b"diff --git a/file b/file\n",
    )
    prepared = load_prepared_task(
        prepared_bundle, SWEbenchTaskContext(instance_id="django__django-11477")
    )
    settings = Settings(
        _env_file=None,
        openai_api_key="test",
        openai_base_url="https://example.test",
        swebench_work_root=tmp_path / "runs",
        swebench_test_timeout_seconds=1,
        swebench_process_timeout_seconds=5,
        swebench_cleanup_timeout_seconds=5,
    )
    connection = await resolve_docker_connection(docker_cli, {})
    evaluator = SWEbenchEvaluator(
        artifact_store=store,
        prepared=prepared,
        settings=settings,
        model_name="test-model",
        executable=harness_executable,
        docker_executable=docker_cli,
        connection=connection,
        environment={},
    )
    request = EvaluationRequest(
        run_id=run_id, task="issue", candidate=CandidateOutput(text="done", patch=patch)
    )
    return evaluator, request, store


@pytest.fixture
def export_engine():
    """External Engine test double; execute the fixture CLI only after guest permission."""
    executable = None
    prefix = ()
    commands = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            self.respond({"Running": False, "ExitCode": 0})

        def respond(self, body, status=200):
            content = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/exec"):
                identifier = uuid4().hex
                commands[identifier] = None
                self.respond({"Id": identifier}, 201)
                return
            self.send_response(101)
            self.send_header("Connection", "Upgrade")
            self.send_header("Upgrade", "tcp")
            self.end_headers()
            self.wfile.flush()
            self.connection.settimeout(5)

            def receive():
                header = self.rfile.read(4)
                if len(header) != 4:
                    raise EOFError
                return json.loads(self.rfile.read(struct.unpack(">I", header)[0]))

            def send(opening, kind, body):
                frame = encode_guest_frame(
                    opening["attempt_id"], opening["execution_id"], kind, body
                )
                self.wfile.write(struct.pack(">BxxxI", 1, len(frame)) + frame)
                self.wfile.flush()

            process = None
            try:
                opening = receive()
                source = bytearray()
                while True:
                    frame = receive()
                    if frame["type"] == "configuration_end":
                        break
                    source.extend(base64.b64decode(frame["body"]["content"]))
                command = json.loads(source)
                send(
                    opening,
                    "guest_identified",
                    {
                        "guest": {
                            "pid": 123,
                            "pgid": 123,
                            "sid": 123,
                            "boot_id": "test",
                            "start_ticks": 1,
                        }
                    },
                )
                assert receive()["type"] == "permit_command"
                assert executable is not None
                process = subprocess.Popen(
                    [str(executable), *prefix, *command["argv"]],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=0.01)
                        break
                    except subprocess.TimeoutExpired:
                        if select.select([self.connection], [], [], 0)[
                            0
                        ] and not self.connection.recv(1):
                            return
                for number, content in ((1, stdout), (2, stderr)):
                    for offset in range(0, len(content), 32768):
                        send(
                            opening,
                            "data",
                            {
                                "stream": number,
                                "content": base64.b64encode(
                                    content[offset : offset + 32768]
                                ).decode(),
                            },
                        )
                send(
                    opening,
                    "completed",
                    {
                        "status": "success" if process.returncode == 0 else "error",
                        "exit_code": process.returncode,
                        "group_ended": True,
                        "issuer_ended": True,
                        "stdout_eof": True,
                        "stderr_eof": True,
                    },
                )
            except (EOFError, BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if process is not None:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=2)
                self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def configure(path, command_prefix=()):
        nonlocal executable, prefix
        executable = path
        prefix = command_prefix
        return DockerConnection(
            None, f"tcp://127.0.0.1:{server.server_port}", None, False, "export-test"
        )

    yield configure
    server.shutdown()
    server.server_close()
    thread.join(5)


pytest_plugins = [
    "tests.support.runtime.persistence",
    "tests.support.cli.fixtures",
    "tests.support.dashboard.fixtures",
]
