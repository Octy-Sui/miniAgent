import argparse
import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from multiprocessing.connection import Connection
from pathlib import Path
from time import monotonic
from uuid import UUID

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
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
from tests.resume_support import StoreBoundary


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

    def log(name, value):
        with (root / name).open("a") as stream:
            stream.write(json.dumps(value) + "\n")

    class Provider:
        async def generate(self, request):
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
            calls = []
            for index in range(3):
                command = f"printf 'c{index}\\n' >> effects.txt"
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

    class Artifacts(FileArtifactStore):
        async def list_for_run(self, run_id):
            if args.gate_finalization:
                channel.send(
                    {
                        "boundary": "finalization",
                        "time": monotonic(),
                        "run_id": str(run_id),
                    }
                )
                assert await receive(channel) == "continue"
            return await super().list_for_run(run_id)

    class AuditStore(StoreBoundary):
        async def initialize(self):
            assert isinstance(self.store, SQLiteRunStore)
            await self.store.initialize()

        async def create(self, state, **kwargs):
            context = kwargs["recovery_context"]
            attachment = context.attachment
            (root / "origin.json").write_text(
                json.dumps(
                    {
                        "run_id": str(state.identity.run_id),
                        "created_at": state.identity.created_at.isoformat(),
                        "workspace_id": attachment.workspace_id,
                        "repository_root": str(attachment.repository_root),
                        "index_sha256": hashlib.sha256(
                            (attachment.repository_root / ".git" / "index").read_bytes()
                        ).hexdigest(),
                        "time": monotonic(),
                    }
                )
            )
            return await super().create(state, **kwargs)

        async def commit(self, change):
            result = await super().commit(change)
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
            return await execute_new_run(
                BootstrapConfig(
                    agent=DEFAULT_AGENT,
                    task="append c0, c1, c2 once, then resume-local-ok",
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
        patch.undo()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("new", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--channel-fd", type=int, required=True)
    parser.add_argument("--label", default="initial")
    parser.add_argument("--run-id")
    parser.add_argument("--gate-call", type=int, default=-1)
    parser.add_argument("--gate-finalization", action="store_true")
    parser.add_argument("--deadline-probe", action="store_true")
    parser.add_argument("--console-approval", action="store_true")
    parser.add_argument(
        "--first-status", choices=("success", "error", "timeout"), default="success"
    )
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
