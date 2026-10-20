import asyncio
import json
import sys

import pytest

from miniagent.environments.contracts import Command

pytestmark = pytest.mark.resume_docker

REATTACH = """
import asyncio,json,sys
from pathlib import Path
from miniagent.artifacts import FileArtifactStore
from miniagent.environments.attachment import DockerAttachment
from miniagent.environments.config import DockerWorkspaceSpec
from miniagent.environments.docker import DockerWorkspaceBackend
from miniagent.environments.contracts import Command
async def main():
 root=Path(sys.argv[1])
 attachment=DockerAttachment.model_validate_json((root/'docker-attachment.json').read_text())
 spec=DockerWorkspaceSpec.model_validate_json((root/'docker-spec.json').read_text())
 backend=DockerWorkspaceBackend(artifact_store=FileArtifactStore(root/'artifacts'),docker_executable=str(root/'docker-audit'))
 workspace=await backend.attach(spec,attachment)
 result=await workspace.execute(Command(argv=['cat','/tmp/miniagent-installed-dependency']))
 (root/'reattach-result.json').write_text(json.dumps({'container_id':workspace.container_id,'exit_code':result.exit_code,'dependency':result.stdout}))
asyncio.run(main())
"""


@pytest.mark.parametrize("status", ["running", "exited"])
async def test_same_container_layer_survives_resume(docker_workspace, tmp_path, status):
    """U21-02: a fresh process attaches the original full container and outside-workdir layer."""
    backend, workspace, _spec, connection = docker_workspace
    attachment = workspace.describe_attachment()
    installed = "dependency-installed-before-interruption"
    result = await workspace.execute(
        Command(
            argv=[
                "sh",
                "-c",
                f"printf '%s' '{installed}' > /tmp/miniagent-installed-dependency",
            ]
        )
    )
    assert result.exit_code == 0
    if status == "exited":
        process = await asyncio.create_subprocess_exec(
            "docker",
            "--host",
            connection.endpoint,
            "stop",
            "--time",
            "1",
            workspace.container_id,
            stdout=asyncio.subprocess.PIPE,
        )
        await process.communicate()
        assert process.returncode == 0
    before = (tmp_path / "docker-cli.jsonl").read_text().splitlines()
    facts = await backend.inspect_attachment(attachment)
    assert facts.identity_matches and facts.container_status == status
    inspected = [
        json.loads(line)
        for line in (tmp_path / "docker-cli.jsonl")
        .read_text()
        .splitlines()[len(before) :]
    ]
    assert not any("start" in call or "exec" in call for call in inspected)
    start = len((tmp_path / "docker-cli.jsonl").read_text().splitlines())
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        REATTACH,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    assert process.returncode == 0, (stdout + stderr).decode()
    result = json.loads((tmp_path / "reattach-result.json").read_text())
    assert result == {
        "container_id": workspace.container_id,
        "exit_code": 0,
        "dependency": installed,
    }
    calls = [
        json.loads(line)
        for line in (tmp_path / "docker-cli.jsonl").read_text().splitlines()[start:]
    ]
    assert not any("create" in call or "pull" in call for call in calls)
    assert sum("start" in call for call in calls) == (status == "exited")


@pytest.mark.parametrize("replacement", [False, True])
async def test_deleted_or_replaced_container_is_rejected(docker_workspace, replacement):
    backend, workspace, _, connection = docker_workspace
    attachment = workspace.describe_attachment()
    process = await asyncio.create_subprocess_exec(
        "docker",
        "--host",
        connection.endpoint,
        "rm",
        "--force",
        workspace.container_id,
        stdout=asyncio.subprocess.PIPE,
    )
    await process.communicate()
    assert process.returncode == 0
    if replacement:
        # A different full ID cannot stand in for the original, even with copied metadata.
        attachment = attachment.model_copy(update={"container_id": "0" * 64})
    facts = await backend.inspect_attachment(attachment)
    assert not facts.exists and facts.errors
