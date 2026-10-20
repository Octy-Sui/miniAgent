import asyncio
import json
import os
import shutil
import signal
import sys
from multiprocessing import Pipe

import pytest

from miniagent.environments.managed_execution import HostProcessIdentity
from miniagent.environments.process import inspect_host_execution
from miniagent.runtime.progress import CandidateDisposition
from tests.support.cli.resume_dry_run import invoke
from tests.support.resume.processes import receive
from tests.support.runtime.runtime import final_response


@pytest.mark.parametrize("ordinal", [1, 2])
@pytest.mark.parametrize("window", ["before", "after"])
async def test_killed_export_caller_never_releases_unconfirmed_host_command(
    resume_case, tmp_path, ordinal, window
):
    application, execution, _ = resume_case
    await execution.record_model_response(
        final_response("candidate"),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    await execution.begin_evaluation("candidate")
    marker = tmp_path / "target-command.jsonl"
    binary = tmp_path / "bin"
    binary.mkdir()
    real_git = shutil.which("git")
    assert real_git
    proxy = binary / "git"
    proxy.write_text(
        f"#!{sys.executable}\n"
        + f"""import json,os,sys,time
from pathlib import Path
if 'GIT_INDEX_FILE' in os.environ:
 with Path({str(marker)!r}).open('a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')
 if 'add' in sys.argv: time.sleep(30)
os.execv({real_git!r},[{real_git!r},*sys.argv[1:]])
"""
    )
    proxy.chmod(0o755)
    environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"])
    parent, child = Pipe()
    deps = application.dependencies
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.cli.host_gate_driver",
        str(deps.run_store.path),
        str(deps.run_id),
        str(tmp_path),
        str(ordinal),
        window,
        str(child.fileno()),
        pass_fds=(child.fileno(),),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    child.close()
    identity = None
    try:
        async with asyncio.timeout(10):
            message = await receive(parent)
        identity = HostProcessIdentity.model_validate(message["identity"])
        record = await deps.run_store.get(deps.run_id)
        operation = record.state.execution.active_operation
        assert operation is not None
        assert len(operation.executions) == ordinal - (window == "before")
        process.kill()
        assert await asyncio.wait_for(process.wait(), 5) == -signal.SIGKILL
        async with asyncio.timeout(3):
            while inspect_host_execution(identity).outcome != "ended":
                await asyncio.sleep(0.01)
        calls = (
            [json.loads(line) for line in marker.read_text().splitlines()]
            if marker.exists()
            else []
        )
        assert len(calls) == ordinal - 1
        code, output = await invoke(deps.run_store.path, deps.run_id, "--dry-run")
        if ordinal == 1 and window == "before":
            assert code == 1 and "WORKSPACE_BUSY" in output, output
        else:
            assert code == 0 and "ready" in output, output
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if identity and inspect_host_execution(identity).outcome == "active":
            os.killpg(identity.pgid, signal.SIGKILL)
        parent.close()
