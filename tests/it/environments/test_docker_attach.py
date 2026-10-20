import json
from pathlib import Path

import pytest

from miniagent.environments.config import DockerOptions, DockerWorkspaceSpec
from miniagent.environments.docker_connection import (
    FrozenDockerConnection,
)


async def test_stopped_inspection_defers_git_without_start(retained, tmp_path):
    backend, attachment, _ = retained
    facts = await backend.inspect_attachment(attachment)
    assert facts.identity_matches and facts.container_status == "exited"
    assert facts.live_checks == ("workdir", "git_baseline", "guest_execution_groups")
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert all("exec" not in call and "start" not in call for call in calls)
    assert calls[-1][-1] == attachment.container_id
    assert all(call[:2] == ["--host", attachment.connection.endpoint] for call in calls)


@pytest.mark.parametrize(
    ("status",), [("running",), ("paused",), ("restarting",), ("removing",), ("dead",)]
)
async def test_container_status_is_checked_without_repairs(retained, tmp_path, status):
    backend, attachment, document = retained
    document["State"]["Status"] = status
    (tmp_path / "inspect.json").write_text(json.dumps(document))
    facts = await backend.inspect_attachment(attachment)
    assert bool(facts.errors) == (status != "running")


@pytest.mark.parametrize(
    ("field",),
    [
        ("Id",),
        ("Image",),
        ("WorkingDir",),
        ("Labels",),
        ("Memory",),
        ("SecurityOpt",),
        ("CapDrop",),
    ],
)
async def test_single_identity_or_security_mismatch_is_rejected(
    retained, tmp_path, field
):
    backend, attachment, document = retained
    if field in ("Id", "Image"):
        document[field] = "replacement"
    elif field in ("WorkingDir", "Labels"):
        document["Config"][field] = {} if field == "Labels" else "/replacement"
    else:
        document["HostConfig"][field] = 0
    (tmp_path / "inspect.json").write_text(json.dumps(document))
    facts = await backend.inspect_attachment(attachment)
    assert not facts.identity_matches and facts.errors


async def test_attach_uses_full_original_id_and_frozen_connection(
    retained, tmp_path, export_engine
):
    backend, attachment, _ = retained
    executable = Path(backend.docker_executable)
    source = executable.read_text().replace("original-daemon", "export-test")
    source = source.replace(
        "else:\n    sys.exit(13)",
        'elif "--show-toplevel" in sys.argv:\n    print("/testbed")\nelif "start" in sys.argv or "/bin/sh" in sys.argv or "cat-file" in sys.argv:\n    pass\nelse:\n    sys.exit(13)',
    )
    executable.write_text(source)
    connection = export_engine(executable)
    attachment = attachment.model_copy(
        update={
            "connection": FrozenDockerConnection(
                source_context="frozen",
                endpoint=connection.endpoint,
                daemon_id=connection.daemon_id,
                cert_path=None,
                tls_verify=False,
            )
        }
    )
    spec = DockerWorkspaceSpec(
        run_id=attachment.run_id,
        options=DockerOptions(image="unused", workdir="/testbed"),
    )
    attached = await backend.attach(spec, attachment)
    assert attached.container_id == attachment.container_id
    assert attached.base_revision == attachment.base_revision
    assert attached.describe_attachment().connection.endpoint == connection.endpoint
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    assert sum("start" in call for call in calls) == 1
    assert all(
        "create" not in call and "pull" not in call and "rm" not in call
        for call in calls
    )
