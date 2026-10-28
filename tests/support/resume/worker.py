import argparse
import asyncio
import base64
import hashlib
import json
import os
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path
from time import monotonic
from uuid import UUID

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.cli import bootstrap as composition
from miniagent.cli import resume as recovery
from miniagent.cli.application import ApplicationResult
from miniagent.cli.bootstrap import BootstrapConfig, execute_new_run
from miniagent.cli.console_approval import ConsoleApprovalHandler
from miniagent.cli.signals import _ForcedSignalCancellation, run_with_signals
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime import RunStatus
from miniagent.runtime.events import RunEventType
from miniagent.runtime.recording import RunPersistenceError
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from tests.support.cli.agent import DEFAULT_AGENT
from tests.support.resume_helpers import StoreBoundary


async def receive(channel):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def readable():
        if not future.done():
            try:
                future.set_result(channel.recv())
            except EOFError as exc:
                future.set_exception(exc)

    loop.add_reader(channel.fileno(), readable)
    try:
        return await future
    finally:
        loop.remove_reader(channel.fileno())


async def run(args, channel) -> int:
    root = args.root
    database = root / "runs.sqlite3"
    manifest = None
    if args.unresolved_manifest:
        from tests.support.resume.fixtures import load_unresolved_manifest

        manifest = load_unresolved_manifest(
            Path(args.unresolved_manifest), before=datetime.now(UTC)
        )
        os.environ.update(
            SWEBENCH_DATA_ROOT=str(manifest.data_root),
            SWEBENCH_WORK_ROOT=str(root / "harness"),
            SWEBENCH_EXECUTABLE=manifest.harness_executable,
            SWEBENCH_ALLOW_IMAGE_PULL="false",
        )

    source = root / "source"
    if args.action == "new" and not source.exists():
        source.mkdir()
        process = await asyncio.create_subprocess_exec(
            "git", "init", "--quiet", str(source)
        )
        assert await process.wait() == 0
        (source / "effects.txt").write_text("")
    prices = root / "prices.toml"
    if not prices.exists():
        prices.write_text(
            '[[models]]\nprovider="qwen"\nmodel="qwen3.8-flash"\ninput_per_unit="1"\noutput_per_unit="2"\n'
        )
    os.environ.update(
        OPENAI_API_KEY="resume-acceptance-fixture",
        OPENAI_BASE_URL="https://provider.example/v1",
        WORKSPACE_BACKEND="local",
        LOCAL_WORKSPACE_ROOT=str(root / "workspaces"),
        ARTIFACT_ROOT=str(root / "artifacts"),
        RUN_DATABASE_PATH=str(database),
        MODEL_PRICING_FILE=str(prices),
    )
    os.environ.pop("EXPORT_TRAJECTORY", None)
    if args.export_env_false:
        os.environ["EXPORT_TRAJECTORY"] = "false"

    def log(name, value):
        with (root / name).open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    class Provider:
        async def generate(self, request):
            if args.before_response:
                channel.send({"boundary": "provider_return", "time": monotonic()})
                assert await receive(channel) == "continue"
            done = any(
                getattr(message, "tool_call_id", None) == "c2"
                for message in request.messages
            )
            log(
                "wire.jsonl",
                {
                    "time": monotonic(),
                    "label": args.label,
                    "response": "final" if done else "batch",
                },
            )
            if done:
                return ModelResponse(
                    message=AssistantMessage(content="resume-local-ok"),
                    finish_reason=FinishReason.STOP,
                    usage=ModelUsage(
                        input_tokens=40, output_tokens=10, total_tokens=50
                    ),
                )
            if manifest is not None:
                encoded = base64.b64encode(manifest.patch_path.read_bytes()).decode()
                program = f"import base64,subprocess; subprocess.run(['git','apply','-'],input=base64.b64decode('{encoded}'),check=True)"
                import shlex

                return ModelResponse(
                    message=AssistantMessage(
                        tool_calls=(
                            ToolCall(
                                id="c2",
                                name="shell",
                                arguments={
                                    "command": "python -c " + shlex.quote(program)
                                },
                            ),
                        )
                    ),
                    finish_reason=FinishReason.TOOL_CALL,
                    usage=ModelUsage(
                        input_tokens=100, output_tokens=20, total_tokens=120
                    ),
                )
            calls = []
            for index in range(3):
                command = f"printf 'c{index}\\n' >> effects.txt"
                if index == 0 and args.output_bytes:
                    import shlex
                    import sys

                    program = f"import sys;sys.stdout.buffer.write(b'x'*{args.output_bytes});sys.stderr.buffer.write(b'y'*{args.output_bytes})"
                    command = (
                        f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}; "
                        + command
                    )
                if index == 0 and args.first_status == "error":
                    command += "; exit 2"
                elif index == 0 and args.first_status == "timeout":
                    command += "; sleep 35"
                calls.append(
                    ToolCall(
                        id=f"c{index}", name="shell", arguments={"command": command}
                    )
                )
            return ModelResponse(
                message=AssistantMessage(tool_calls=tuple(calls)),
                finish_reason=FinishReason.TOOL_CALL,
                usage=ModelUsage(input_tokens=100, output_tokens=20, total_tokens=120),
            )

    class Human:
        async def resolve(self, request, decision):
            if decision.request.call_index == args.gate_call:
                channel.send(
                    {
                        "boundary": "approval",
                        "call_index": args.gate_call,
                        "run_id": str(request.run_id),
                        "time": monotonic(),
                    }
                )
                assert await receive(channel) == "continue"
            return ApprovalResolution(
                approval_id=request.approval_id,
                outcome=ApprovalOutcome.APPROVED,
                actor_kind="human",
                actor_id="acceptance-human",
                reason="Explicit approval for owned acceptance command",
                resolved_at=datetime.now(UTC),
            )

    finalization_seen = [False]
    trajectory_gate_reached = [False]
    signal_notifiers = []

    class Artifacts(FileArtifactStore):
        finalization_gated = False

        async def list_records_for_run(self, run_id, *, cursor=None, limit=100):
            finalization_seen[0] = True
            if args.gate_finalization and not self.finalization_gated:
                self.finalization_gated = True
                channel.send(
                    {
                        "boundary": "finalization",
                        "time": monotonic(),
                        "run_id": str(run_id),
                    }
                )
                assert await receive(channel) == "continue"
            return await super().list_records_for_run(
                run_id, cursor=cursor, limit=limit
            )

    class AuditStore(StoreBoundary):
        async def list_events(self, *values, **options):
            if (
                args.trajectory_gate == "encoding"
                and finalization_seen[0]
                and not trajectory_gate_reached[0]
            ):
                trajectory_gate_reached[0] = True
                channel.send({"boundary": "trajectory_encoding", "time": monotonic()})
                assert await receive(channel) == "continue"
            return await super().list_events(*values, **options)

        async def initialize(self):
            assert isinstance(self.store, SQLiteRunStore)
            await self.store.initialize()

        async def create(self, state, **kwargs):
            context = kwargs["recovery_context"]
            attachment = context.attachment
            metadata = {
                "run_id": str(state.identity.run_id),
                "created_at": state.identity.created_at.isoformat(),
                "workspace_id": attachment.workspace_id,
                "time": monotonic(),
            }
            if attachment.kind == "local":
                metadata.update(
                    repository_root=str(attachment.repository_root),
                    index_sha256=hashlib.sha256(
                        (attachment.repository_root / ".git" / "index").read_bytes()
                    ).hexdigest(),
                )
            else:
                metadata.update(
                    container_id=attachment.container_id,
                    daemon_id=attachment.connection.daemon_id,
                )
            (root / "origin.json").write_text(json.dumps(metadata))
            return await super().create(state, **kwargs)

        async def commit(self, change):
            result = await super().commit(change)
            if args.gate_harness and any(
                getattr(getattr(event.payload, "identity", None), "role", None)
                == "harness"
                for event in change.events
            ):
                channel.send(
                    {
                        "boundary": "harness",
                        "sequence": result.record.last_event_sequence,
                        "time": monotonic(),
                    }
                )
                assert await receive(channel) == "continue"

            log(
                "commits.jsonl",
                {
                    "label": args.label,
                    "time": monotonic(),
                    "sequence": result.record.last_event_sequence,
                    "types": [event.type.value for event in change.events],
                },
            )
            return result

    patch = pytest.MonkeyPatch()
    if args.transaction_event:
        import sqlite3

        connect = sqlite3.connect

        class TransactionConnection(sqlite3.Connection):
            def execute(self, sql, parameters=(), /):
                cursor = super().execute(sql, parameters)
                if (
                    "INSERT INTO run_events" in sql
                    and len(parameters) > 4
                    and parameters[4] == args.transaction_event
                ):
                    channel.send(
                        {
                            "boundary": "transaction",
                            "event": parameters[4],
                            "time": monotonic(),
                        }
                    )
                    assert channel.recv() == "continue"
                return cursor

        def transaction_connect(*values, **options):
            return connect(*values, **options, factory=TransactionConnection)

        patch.setattr(sqlite3, "connect", transaction_connect)
    if args.trajectory_gate in ("body", "manifest"):
        write, replace = os.write, os.replace

        def gate_publication():
            trajectory_gate_reached[0] = True
            channel.send(
                {"boundary": "trajectory_" + args.trajectory_gate, "time": monotonic()}
            )
            assert channel.recv() == "continue"

        def write_body(fd, content):
            if (
                args.trajectory_gate == "body"
                and not trajectory_gate_reached[0]
                and b"miniagent.trajectory" in bytes(content[:120])
            ):
                gate_publication()
            return write(fd, content)

        def replace_manifest(source, target):
            if (
                args.trajectory_gate == "manifest"
                and not trajectory_gate_reached[0]
                and str(target).endswith(".json")
                and json.loads(Path(source).read_bytes()).get("type")
                == "run_trajectory"
            ):
                gate_publication()
            return replace(source, target)

        patch.setattr(os, "write", write_body)
        patch.setattr(os, "replace", replace_manifest)

    patch.setattr(composition, "build_provider", lambda agent, settings: Provider())
    patch.setattr(composition, "FileArtifactStore", Artifacts)
    # Console input is an external boundary; the restored application must ask it again.
    if not args.console_approval:
        patch.setattr(recovery, "ConsoleApprovalHandler", Human)

    def store(path, **kwargs):
        real = SQLiteRunStore(path, **kwargs)
        if kwargs.get("read_only"):
            return real
        target = (
            RunEventType(args.fault_event)
            if args.fault_event and args.fault_event != "create"
            else RunEventType.PROVIDER_IDENTITY_CHANGED
        )
        return AuditStore(
            real,
            action=args.fault_action,
            operation="create" if args.fault_event == "create" else "commit",
            event_type=None if args.fault_event == "create" else target,
            channel=channel,
        )

    patch.setattr(composition, "SQLiteRunStore", store)
    patch.setattr(recovery, "SQLiteRunStore", store)

    async def operation(cancellation, interruption):
        if args.trajectory_gate:

            async def signal_notifier():
                await interruption.wait()
                channel.send({"boundary": "first_signal", "time": monotonic()})

            signal_notifiers.append(asyncio.create_task(signal_notifier()))
        if args.deadline_probe:
            channel.send({"boundary": "deadline", "time": monotonic()})
            await interruption.wait()
            for name in ("cleanup", "diagnostic"):
                await asyncio.sleep(10)
                channel.send(
                    {
                        "boundary": name,
                        "deadline": interruption.deadline,
                        "time": monotonic(),
                    }
                )
            await asyncio.Event().wait()
        if args.action == "new":
            from miniagent.environments.config import WorkspaceKind
            from miniagent.evaluation.swebench_data import (
                SWEbenchTaskContext,
                load_prepared_task,
            )

            if manifest is not None:
                prepared = load_prepared_task(
                    manifest.data_root,
                    SWEbenchTaskContext(
                        instance_id=manifest.instance_id,
                        evaluation_image=manifest.image_id,
                    ),
                )
                assert prepared.record_sha256 == manifest.task_record_sha256
                assert prepared.record.fail_to_pass == manifest.fail_to_pass
                assert prepared.record.pass_to_pass == manifest.pass_to_pass
                from miniagent.environments.docker_connection import (
                    resolve_docker_connection,
                )

                connection = await resolve_docker_connection(
                    Path("docker"), dict(os.environ)
                )
                assert connection.daemon_id == manifest.daemon_id

            return await execute_new_run(
                BootstrapConfig(
                    agent=DEFAULT_AGENT,
                    trajectory=args.trajectory,
                    task=manifest.task
                    if manifest
                    else "append c0, c1, c2 once, then resume-local-ok",
                    workspace_kind=WorkspaceKind.DOCKER
                    if manifest
                    else WorkspaceKind.LOCAL,
                    docker_image=manifest.image_id if manifest else None,
                    swebench_task=SWEbenchTaskContext(
                        instance_id=manifest.instance_id,
                        evaluation_image=manifest.image_id,
                    )
                    if manifest
                    else None,
                    source_dir=source,
                    approval_handler=ConsoleApprovalHandler()
                    if args.console_approval
                    else Human(),
                ),
                cancellation_token=cancellation,
                interruption_token=interruption,
            )
        return await recovery.resume_application(
            recovery.ResumeConfig(run_id=UUID(args.run_id), database=database),
            cancellation_token=cancellation,
            interruption_token=interruption,
        )

    try:
        result, received = await run_with_signals(operation)
        assert isinstance(result, ApplicationResult)
        (root / f"result-{args.label}.json").write_text(
            result.model_dump_json(indent=2)
        )
        log("lifecycle.jsonl", {"label": args.label, "returned": monotonic()})
        return (
            128 + received.value
            if received
            else 0
            if result.run_result.status is RunStatus.SUCCEEDED
            and not result.finalization_errors
            else 1
        )
    except _ForcedSignalCancellation as exc:
        return 128 + exc.received_signal.value
    except RunPersistenceError as exc:
        (root / f"result-{args.label}.json").write_text(
            json.dumps(
                {
                    "persistence_error": True,
                    "last_confirmed_sequence": exc.last_confirmed_sequence,
                }
            )
        )
        return 1
    finally:
        for notifier in signal_notifiers:
            notifier.cancel()
        await asyncio.gather(*signal_notifiers, return_exceptions=True)
        patch.undo()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("new", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--channel-fd", type=int, required=True)
    parser.add_argument("--label", default="initial")
    parser.add_argument("--run-id")
    parser.add_argument("--export-env-false", action="store_true")
    parser.add_argument("--output-bytes", type=int, default=0)
    parser.add_argument("--trajectory-gate", choices=("encoding", "body", "manifest"))
    parser.add_argument("--before-response", action="store_true")
    parser.add_argument("--transaction-event")
    parser.add_argument(
        "--trajectory", action=argparse.BooleanOptionalAction, default=None
    )
    parser.add_argument("--gate-call", type=int, default=-1)
    parser.add_argument("--gate-finalization", action="store_true")
    parser.add_argument("--deadline-probe", action="store_true")
    parser.add_argument("--console-approval", action="store_true")
    parser.add_argument(
        "--first-status", choices=("success", "error", "timeout"), default="success"
    )
    parser.add_argument("--unresolved-manifest")
    parser.add_argument("--gate-harness", action="store_true")
    parser.add_argument("--fault-event")
    parser.add_argument("--fault-action", default="pause_after_commit")
    args = parser.parse_args()
    channel = Connection(args.channel_fd)
    try:
        raise SystemExit(asyncio.run(run(args, channel)))
    finally:
        channel.close()


if __name__ == "__main__":
    main()
