import json

import pytest

from miniagent.artifacts import FileArtifactStore
from tests.support.resume.processes import spawn_worker

__all__ = ["spawn_worker"]


@pytest.mark.parametrize("enabled", [None, False, True])
async def test_evidence_is_always_captured_and_survives_workspace_cleanup(
    spawn_worker, enabled
):
    worker = await spawn_worker(trajectory=enabled, export_env_false=enabled is False)
    code, output = await worker.finish()
    assert code == 0, output
    from tests.support.resume.local import stored

    record, events = await stored(worker.root)
    results = [event.payload for event in events if event.type.value == "tool_end"]
    assert len(results) == 3
    assert all(
        result.raw_result is not None and result.observation is not None
        for result in results
    )
    store = FileArtifactStore(worker.root / "artifacts")
    catalog = (await store.list_records_for_run(record.state.identity.run_id)).records
    trajectories = [item for item in catalog if item.type == "run_trajectory"]
    assert len(trajectories) == int(bool(enabled))
    outputs = [
        item for item in catalog if item.type in ("command_stdout", "command_stderr")
    ]
    assert len(outputs) == 6
    for item in outputs:
        body = await store.read(item.reference())
        assert len(body) == item.size_bytes
        if item.type == "command_stdout":
            assert body == b""
    if enabled:
        document = json.loads(await store.read(trajectories[0].reference()))
        assert document["summary"]["final_output"] == "resume-local-ok"
        assert document["coverage"]["status"] == "complete"


@pytest.mark.parametrize("normalization_failure", [False, True])
def test_forbidden_provider_fields_do_not_enter_evidence(
    configured_host, monkeypatch, provider_endpoint, normalization_failure
):
    import os
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path
    from urllib.parse import urlparse

    from tests.support.cli.bootstrap import create_source_repository

    sentinel = "forbidden-reasoning-and-credential-91ac"
    allowed = "普通解释 api_key=allowed-body"
    from pydantic import JsonValue

    message: dict[str, JsonValue] = {
        "role": "assistant",
        "content": allowed,
        "reasoning_content": sentinel,
        "thinking": sentinel,
    }
    if normalization_failure:
        message["tool_calls"] = [
            {
                "id": "call",
                "type": "function",
                "function": {"name": "shell", "arguments": "{"},
            }
        ]
    provider_endpoint.message = message
    root = Path.cwd()
    source = create_source_repository(configured_host / "source")
    environment = dict(
        os.environ,
        OPENAI_BASE_URL=provider_endpoint.url,
        OPENAI_API_KEY=sentinel,
        PYTHONPATH=str(root / "src"),
        MODEL_LIMITS_FILE=str(root / "config/model_limits.toml"),
        MODEL_PRICING_FILE=str(root / "config/model_pricing.toml"),
    )
    result = subprocess.run(
        [sys.executable, "-m", "miniagent.cli.main", "task", "--trajectory"],
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == int(normalization_failure), (
        result.stdout + result.stderr
    )
    assert sentinel not in result.stdout + result.stderr
    with sqlite3.connect(configured_host / "journal.sqlite3") as connection:
        payloads = [
            row[0] for row in connection.execute("SELECT payload_json FROM run_events")
        ]
    assert sentinel not in "".join(payloads)
    assert allowed in "".join(payloads)
    manifests = [
        json.loads(path.read_text())
        for path in (configured_host / "artifacts/manifests").rglob("*.json")
    ]
    trajectory = next(
        record for record in manifests if record["type"] == "run_trajectory"
    )
    assert result.stdout.splitlines()[-1] == f"Trajectory: {trajectory['uri']}"
    document = json.loads(Path(urlparse(trajectory["uri"]).path).read_bytes())
    assert sentinel not in json.dumps(document)
    responded = next(
        e["payload"]
        for e in document["events"]
        if e["payload"]["type"] == "model_responded"
    )
    evidence = (
        responded["normalization_failure"]
        if normalization_failure
        else responded["response"]
    )
    assert (
        evidence["content"] == allowed
        if normalization_failure
        else evidence["message"]["content"] == allowed
    )
    for record in manifests:
        assert sentinel.encode() not in Path(urlparse(record["uri"]).path).read_bytes()
    assert len(provider_endpoint.requests) == 1


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("size", [8 * 1024 * 1024, 64 * 1024 * 1024])
async def test_complete_output_is_independent_of_json_limit(
    spawn_worker, size, enabled
):
    import hashlib

    from tests.support.resume.local import stored

    worker = await spawn_worker(
        trajectory=enabled, no_trajectory=not enabled, output_bytes=size
    )
    code, output = await worker.finish(timeout=20)
    assert code == 0, output
    record, _events = await stored(worker.root)
    store = FileArtifactStore(worker.root / "artifacts")
    records = (await store.list_records_for_run(record.state.identity.run_id)).records
    stdout = next(
        item
        for item in records
        if item.type == "command_stdout" and item.size_bytes == size
    )
    assert stdout.sha256 == hashlib.sha256(b"x" * size).hexdigest()
    assert await store.read(stdout.reference()) == b"x" * size
    stderr = next(
        item
        for item in records
        if item.type == "command_stderr" and item.size_bytes >= size
    )
    assert (await store.read(stderr.reference())).endswith(b"y" * size)
    assert len([event for event in _events if event.type.value == "tool_end"]) == 3
    if not enabled:
        assert not any(item.type == "run_trajectory" for item in records)
        return
    trajectory = next(item for item in records if item.type == "run_trajectory")
    document = json.loads(await store.read(trajectory.reference()))
    assert trajectory.size_bytes < 67_108_864
    assert document["coverage"]["status"] == "complete"
    assert any(
        item["artifact_id"] == str(stdout.artifact_id)
        and item["relation"] == "event_referenced"
        for item in document["artifacts"]
    )


@pytest.mark.parametrize("fault", ["export_storage", "boundary_unavailable"])
async def test_cli_displays_export_diagnostic_without_replacing_original_error(
    configured_host, provider_endpoint, fault
):
    import asyncio
    import os
    import sqlite3
    import sys
    import threading
    from pathlib import Path

    from tests.support.cli.bootstrap import create_source_repository

    root = Path.cwd()
    source = create_source_repository(configured_host / "source")
    provider_endpoint.before_response = threading.Event()
    provider_endpoint.release_response = threading.Event()
    environment = dict(
        os.environ,
        OPENAI_BASE_URL=provider_endpoint.url,
        PYTHONPATH=str(root / "src"),
        MODEL_LIMITS_FILE=str(root / "config/model_limits.toml"),
        MODEL_PRICING_FILE=str(root / "config/model_pricing.toml"),
    )
    database = configured_host / "journal.sqlite3"
    saved = configured_host / "saved.db"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "miniagent.cli.main",
        "task",
        "--trajectory",
        cwd=source,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert await asyncio.to_thread(provider_endpoint.before_response.wait, 5)
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM run_events WHERE type='model_requested'"
                ).fetchone()[0]
                == 1
            )
            if fault == "export_storage":
                connection.execute(
                    "CREATE TRIGGER fail_response BEFORE INSERT ON run_events WHEN NEW.type='model_responded' BEGIN SELECT RAISE(ABORT, 'original commit fault'); END"
                )
        connection.close()
        if fault == "export_storage":
            artifact_root = configured_host / "artifacts"
            artifact_root.mkdir(exist_ok=True)
            (artifact_root / "staging").write_text("export staging unavailable")
        else:
            database.rename(saved)
    finally:
        provider_endpoint.release_response.set()
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 1, stderr.decode()
        assert b"Failed to persist run" in stderr
        assert (
            b"TRAJECTORY_EXPORT_FAILED"
            if fault == "export_storage"
            else b"TRAJECTORY_EXPORT_SKIPPED"
        ) in stderr
        assert b"Trajectory: " not in stdout
        assert len(provider_endpoint.requests) == 1
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if saved.exists():
            saved.rename(database)
