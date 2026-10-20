from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.contracts import Command
from miniagent.environments.docker_exec import run_docker_exec_output
from miniagent.environments.managed_execution import ManagedExecutionError
from miniagent.environments.output_capture import OutputCapture
from tests.support.environments.docker_exec_helper import Observer


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
