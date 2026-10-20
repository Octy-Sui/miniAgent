import asyncio
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    Command,
    ExecutionStatus,
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
    LocalWorkspaceState,
)
from miniagent.tools.contracts import ToolStatus
from miniagent.tools.read_file import ReadFileArguments, ReadFileTool
from miniagent.tools.shell import ShellArguments, ShellTool
from tests.support.environments.local import local_backend


async def test_export_without_arguments_remains_supported(tmp_path, source_repository):
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    (workspace.root / "tracked.txt").write_text("changed\n")
    assert (await workspace.export_patch()).files == ["tracked.txt"]


async def test_export_lock_wait_counts_against_budget(tmp_path, source_repository):
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    executing = asyncio.create_task(
        workspace.execute(Command(argv=["/bin/sh", "-c", "touch busy; sleep 2"]))
    )
    while not (workspace.root / "busy").exists():
        await asyncio.sleep(0.01)
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            await workspace.export_patch(
                timeout_seconds=0.2, cleanup_timeout_seconds=0.05
            )
        assert monotonic() - started < 0.5
    finally:
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing


async def test_workspace_snapshots_current_source_without_modifying_it(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    (source_repository / "tracked.txt").write_text("working tree\n", encoding="utf-8")
    (source_repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )

    try:
        assert (
            await workspace.execute(Command(argv=["cat", "tracked.txt"]))
        ).stdout == "working tree\n"
        assert (
            await workspace.execute(Command(argv=["cat", "untracked.txt"]))
        ).stdout == "untracked\n"
        await workspace.execute(
            Command(argv=["/bin/sh", "-c", "printf agent > agent.txt"])
        )

        patch = await workspace.export_patch()

        assert patch.files == ["agent.txt"]
        assert "agent.txt" in patch.content
        assert not (source_repository / "agent.txt").exists()
    finally:
        await workspace.destroy()


async def test_workspaces_do_not_share_file_changes(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    backend = local_backend(tmp_path)
    first = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    second = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )

    try:
        await first.execute(
            Command(argv=["/bin/sh", "-c", "printf first > tracked.txt"])
        )

        assert (
            await second.execute(Command(argv=["cat", "tracked.txt"]))
        ).stdout == "committed\n"
    finally:
        await first.destroy()
        await second.destroy()


async def test_checkpoint_can_restore_and_recover_workspace(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    backend = local_backend(tmp_path)
    spec = LocalWorkspaceSpec(
        run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
    )
    workspace = await backend.create(spec)
    await workspace.execute(
        Command(argv=["/bin/sh", "-c", "printf checkpoint > result.txt"])
    )
    checkpoint = await workspace.checkpoint()
    assert checkpoint.artifact is not None
    record = await backend.artifact_store.get_record(checkpoint.artifact)
    assert record.metadata == {
        "backend": "local",
        "checkpoint_id": checkpoint.checkpoint_id,
        "workspace_id": checkpoint.workspace_id,
        "revision": checkpoint.revision,
        "base_revision": checkpoint.base_revision,
        "workdir": ".",
    }
    await workspace.execute(
        Command(argv=["/bin/sh", "-c", "printf changed > result.txt"])
    )

    await workspace.restore(checkpoint)
    recovered = await backend.recover(spec, checkpoint)
    try:
        assert (
            await workspace.execute(Command(argv=["cat", "result.txt"]))
        ).stdout == "checkpoint"
        assert (
            await recovered.execute(Command(argv=["cat", "result.txt"]))
        ).stdout == "checkpoint"
    finally:
        await workspace.destroy()
        await recovered.destroy()


async def test_timeout_kills_descendant_processes(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    marker = workspace.root / "late.txt"

    try:
        result = await workspace.execute(
            Command(
                argv=[
                    "/bin/sh",
                    "-c",
                    "(sleep 0.2; printf late > late.txt) & wait",
                ],
                timeout_seconds=0.05,
            )
        )
        await asyncio.sleep(0.3)

        assert result.status is ExecutionStatus.TIMEOUT
        assert workspace.state is LocalWorkspaceState.BROKEN
        assert not marker.exists()
    finally:
        await workspace.destroy()


async def test_read_file_tool_preserves_file_error_contracts(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    (source_repository / "binary.bin").write_bytes(b"\xff")
    workspace = await local_backend(tmp_path).create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )
    tool = ReadFileTool(workspace)

    try:
        missing = await tool.execute(
            ReadFileArguments(path="missing.txt"), tool_call_id="missing"
        )
        binary = await tool.execute(
            ReadFileArguments(path="binary.bin"), tool_call_id="binary"
        )
        escaped = await tool.execute(
            ReadFileArguments(path="../outside"), tool_call_id="escaped"
        )

        assert missing.error is not None
        assert missing.error.code == "FILE_NOT_FOUND"
        assert binary.error is not None
        assert binary.error.code == "DECODE_ERROR"
        assert escaped.status is ToolStatus.DENIED
    finally:
        await workspace.destroy()


async def test_read_file_tool_reports_truncated_content(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    (source_repository / "large.txt").write_text("content exceeds limit")
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces",
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        max_output_bytes=8,
    )
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )

    try:
        result = await ReadFileTool(workspace).execute(
            ReadFileArguments(path="large.txt"),
            tool_call_id="large",
        )

        assert result.status is ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == "OUTPUT_TRUNCATED"
        assert result.data is not None
        assert result.data["truncated"] is True
        assert result.output.stdout_truncated
    finally:
        await workspace.destroy()


async def test_shell_tool_preserves_success_when_output_is_truncated(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    backend = LocalWorkspaceBackend(
        root=tmp_path / "workspaces",
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        max_output_bytes=4,
    )
    workspace = await backend.create(
        LocalWorkspaceSpec(
            run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
        )
    )

    try:
        result = await ShellTool(workspace).execute(
            ShellArguments(command="printf 123456"),
            tool_call_id="large",
        )

        assert result.status is ToolStatus.SUCCESS
        assert result.output.stdout_truncated
        assert result.error is None
    finally:
        await workspace.destroy()
