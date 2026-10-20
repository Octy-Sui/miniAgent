import asyncio
import os
import sys
from pathlib import Path

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli import bootstrap as composition
from miniagent.cli.bootstrap import BootstrapConfig, execute_new_run
from miniagent.cli.signals import _ForcedSignalCancellation, run_with_signals
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.storage import SQLiteRunStore


def ready(value="ready"):
    print(value, flush=True)


async def run(mode: str, root: Path) -> int:
    source = root / "source"
    source.mkdir()
    initializing = await asyncio.create_subprocess_exec(
        "git", "init", "--quiet", str(source)
    )
    assert await initializing.wait() == 0
    (source / "source.txt").write_text("original\n")
    os.environ.update(
        OPENAI_API_KEY="signal-fixture",
        OPENAI_BASE_URL="https://example.test/v1",
        LOCAL_WORKSPACE_ROOT=str(root / "workspaces"),
        ARTIFACT_ROOT=str(root / "artifacts"),
        RUN_DATABASE_PATH=str(root / "runs.sqlite3"),
    )

    class Provider:
        async def generate(self, request):
            if mode == "shell":
                import shlex

                from miniagent.tools.contracts import ToolCall

                program = f"from pathlib import Path;import time;Path({str(root / 'shell-ready')!r}).write_text('ready');time.sleep(60)"
                return ModelResponse(
                    message=AssistantMessage(
                        tool_calls=(
                            ToolCall(
                                id="shell-call",
                                name="shell",
                                arguments={
                                    "command": shlex.join(
                                        [sys.executable, "-c", program]
                                    )
                                },
                            ),
                        )
                    ),
                    finish_reason=FinishReason.TOOL_CALL,
                    usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
            if mode in ("model", "summary"):
                ready()
                await asyncio.Event().wait()
            return ModelResponse(
                message=AssistantMessage(content="done"),
                finish_reason=FinishReason.STOP,
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    def provider(agent, settings):
        if mode == "finalization":
            for path in settings.local_workspace_root.rglob("source.txt"):
                path.write_text("changed\n")
        return Provider()

    boundary = pytest.MonkeyPatch()
    boundary.setattr(composition, "build_provider", provider)

    class GateArtifacts(FileArtifactStore):
        async def put(self, request, content, *, deadline=None):
            if mode == "finalization":
                ready()
                await asyncio.Event().wait()
            return await super().put(request, content, deadline=deadline)

    boundary.setattr(composition, "FileArtifactStore", GateArtifacts)

    class GateStore:
        def __init__(self, store):
            self.store = store

        def __getattr__(self, name):
            return getattr(self.store, name)

        async def create(self, state, **kwargs):
            ready()
            await asyncio.Event().wait()

    if mode == "before_record":
        boundary.setattr(
            composition,
            "SQLiteRunStore",
            lambda path, **kwargs: (
                SQLiteRunStore(path, **kwargs)
                if kwargs.get("read_only")
                else GateStore(SQLiteRunStore(path, **kwargs))
            ),
        )

    class Approve:
        async def resolve(self, request, decision):
            from datetime import UTC, datetime

            from miniagent.policy import ApprovalOutcome, ApprovalResolution

            return ApprovalResolution(
                approval_id=request.approval_id,
                outcome=ApprovalOutcome.APPROVED,
                actor_kind="human",
                actor_id="signal-fixture",
                reason="Owned test command",
                resolved_at=datetime.now(UTC),
            )

    async def shell_ready():
        while not (root / "shell-ready").exists():
            await asyncio.sleep(0.005)
        ready()

    async def operation(cancellation, interruption):
        if mode == "summary":
            from miniagent.cli.application import ApplicationResult
            from tests.support.runtime.compaction_resume import failed_summary

            runtime, execution, _store = await failed_summary(root, 0)
            runtime.provider = Provider()
            return ApplicationResult(
                run_result=await runtime._drive(
                    execution, interruption_token=interruption
                )
            )
        if mode == "attach":
            from miniagent.cli import resume as recovery
            from miniagent.environments import LocalWorkspaceBackend
            from tests.support.cli.resume import retained

            application, _execution, _provider = await retained(source, boundary)

            class GateBackend(LocalWorkspaceBackend):
                async def attach(self, spec, attachment):
                    ready()
                    await asyncio.Event().wait()

            boundary.setattr(recovery, "LocalWorkspaceBackend", GateBackend)
            return await recovery.resume_application(
                recovery.ResumeConfig(
                    run_id=application.dependencies.run_id,
                    database=root / "runs.sqlite3",
                ),
                cancellation_token=cancellation,
                interruption_token=interruption,
            )
        if mode == "second":
            ready()
            await interruption.wait()
            ready("first_seen")
            await asyncio.Event().wait()
        notifier = asyncio.create_task(shell_ready()) if mode == "shell" else None
        try:
            return await execute_new_run(
                BootstrapConfig(
                    agent=DEFAULT_AGENT,
                    task="task",
                    source_dir=source,
                    approval_handler=Approve(),
                ),
                cancellation_token=cancellation,
                interruption_token=interruption,
            )
        finally:
            if notifier is not None:
                notifier.cancel()
                from contextlib import suppress

                with suppress(asyncio.CancelledError):
                    await notifier

    try:
        result, received = await run_with_signals(operation)
        ready(f"status={result.run_result.status.value}")
        return 128 + received.value if received else 0
    except _ForcedSignalCancellation as exc:
        ready("signal-stop")
        return 128 + exc.received_signal.value
    finally:
        boundary.undo()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1], Path(sys.argv[2]))))
