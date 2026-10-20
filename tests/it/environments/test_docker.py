import asyncio
import os
from time import monotonic

import psutil
import pytest

from miniagent.environments import (
    DockerWorkspaceBackend,
)
from miniagent.environments.contracts import PatchExportCleanupError
from tests.support.environments.docker import (
    UnusedArtifactStore,
)


async def test_export_patch_shares_budget_with_cleanup(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("hang")
    started = monotonic()
    with pytest.raises(PatchExportCleanupError, match="cleanup|Cleanup"):
        await export_workspace.export_patch(
            timeout_seconds=8.0, cleanup_timeout_seconds=0.5
        )
    assert monotonic() - started < 11.0
    pid = int((tmp_path / "cleanup-started").read_text())
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    await asyncio.to_thread(process.wait, timeout=2)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_cancel_during_index_cleanup_preserves_cancellation(
    export_workspace, tmp_path
):
    (tmp_path / "mode").write_text("hang")
    exporting = asyncio.create_task(
        export_workspace.export_patch(timeout_seconds=8.0, cleanup_timeout_seconds=0.5)
    )
    while not (tmp_path / "cleanup-started").exists() and not exporting.done():
        await asyncio.sleep(0.01)
    exporting.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await exporting
    assert "index" in " ".join(caught.value.__notes__)


async def test_cleanup_failure_rejects_generated_diff(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("error")
    with pytest.raises(PatchExportCleanupError, match="cleanup|Cleanup"):
        await export_workspace.export_patch(
            timeout_seconds=8.0, cleanup_timeout_seconds=0.5
        )


async def test_docker_command_output_is_bounded_while_stream_is_drained() -> None:
    backend = DockerWorkspaceBackend(
        artifact_store=UnusedArtifactStore(),
        docker_executable="/bin/sh",
        max_output_bytes=4,
    )

    result = await backend._run(["-c", "printf 123456"])

    assert result.stdout == b"1234"
    assert result.stdout_truncated
