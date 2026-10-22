from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments import (
    DockerWorkspace,
    DockerWorkspaceBackend,
    DockerWorkspaceState,
    ExecutionStatus,
)
from miniagent.environments.contracts import Command
from miniagent.environments.docker_exec import run_docker_exec_output
from miniagent.environments.managed_execution import ManagedExecutionError
from miniagent.environments.output_capture import OutputCapture
from tests.support.environments.docker import workspace_spec
from tests.support.environments.docker_exec_helper import Observer


async def test_verified_timeout_preserves_workspace_for_commands_and_patch_export(
    engine, tmp_path
):
    store = FileArtifactStore(tmp_path / "artifacts")
    backend = DockerWorkspaceBackend(
        artifact_store=store,
        connection=engine.connection,
        docker_executable="/usr/bin/true",
    )
    workspace = DockerWorkspace(
        backend=backend,
        spec=workspace_spec(),
        _workspace_id="workspace-id",
        container_id="a" * 64,
        image_id="sha256:image",
        base_revision="abc123",
    )
    engine.output_gate.set()
    engine.output_chunks = [(1, b"before timeout"), (2, b"diagnostic")]
    engine.completion.update(
        status="timeout", exit_code=None, stdout_eof=False, stderr_eof=False
    )

    result = await workspace.execute(Command(argv=["sleep", "60"]))

    assert result.status is ExecutionStatus.TIMEOUT
    assert workspace.state is DockerWorkspaceState.ACTIVE
    assert result.stdout == "before timeout"
    assert result.stderr == "diagnostic"
    assert result.output_evidence is not None
    assert result.output_evidence.stdout.status == "partial"
    assert result.output_evidence.stdout.ref is not None
    assert await store.read(result.output_evidence.stdout.ref) == b"before timeout"

    engine.completion.update(
        status="success", exit_code=0, stdout_eof=True, stderr_eof=True
    )
    engine.output_chunks = [(1, b"still active")]
    assert (
        await workspace.execute(Command(argv=["printf", "still active"]))
    ).stdout == "still active"

    engine.output_chunks = []
    assert (await workspace.export_patch()).content == ""


@pytest.mark.parametrize(
    "variant",
    [
        "binary",
        "utf8",
        "empty",
        "incomplete",
        "wrong_scope",
        "duplicate_completion",
        "timeout",
    ],
)
async def test_tc_u09_05(engine, tmp_path, variant):
    expected = bytes(range(256)) * 1024
    if variant == "utf8":
        expected = ("汉字🙂" * 20000).encode()
    elif variant == "empty":
        expected = b""
    engine.output_chunks = [
        (1, expected[offset : offset + 32767])
        for offset in range(0, len(expected), 32767)
    ] + [(2, b"stderr")]
    engine.guest_completes = variant != "incomplete"
    engine.wrong_scope = variant == "wrong_scope"
    engine.duplicate_completion = variant == "duplicate_completion"
    if variant == "timeout":
        engine.completion.update(
            status="timeout", exit_code=None, stdout_eof=False, stderr_eof=False
        )
    store = FileArtifactStore(tmp_path / "artifacts")
    observer = Observer(engine)
    capture = await OutputCapture.open(
        store=store, run_id=uuid4(), attempt_id=observer.attempt_id, deadline=None
    )
    command = Command(argv=["printf", "business"], cwd="/testbed")
    try:
        if variant in {"incomplete", "wrong_scope", "duplicate_completion"}:
            with pytest.raises(ManagedExecutionError):
                await run_docker_exec_output(
                    command,
                    connection=engine.connection,
                    container_id="a" * 64,
                    observer=observer,
                    attempt_id=observer.attempt_id,
                    deadline=monotonic() + 5,
                    max_output_bytes=1024,
                    output_capture=capture,
                )
            output = next(iter(observer.outputs.values()))
            assert output.stdout.status == "partial"
            assert not output.stdout.eof_observed
        else:
            result = await run_docker_exec_output(
                command,
                connection=engine.connection,
                container_id="a" * 64,
                observer=observer,
                attempt_id=observer.attempt_id,
                deadline=monotonic() + 5,
                max_output_bytes=1024,
                output_capture=capture,
            )
            output = result.result.output_evidence
            assert output is not None
            assert output.stdout.ref is not None
            assert output.stderr.ref is not None
            assert await store.read(output.stdout.ref) == expected
            assert await store.read(output.stderr.ref) == b"stderr"
            assert output.stdout.status == (
                "partial" if variant == "timeout" else "complete"
            )
            assert result.result.stdout_truncated == (len(expected) > 1024)
            assert observer.commands[output.execution_id].argv == tuple(command.argv)
            assert observer.commands[output.execution_id].cwd == "/testbed"
    finally:
        await capture.abort()
