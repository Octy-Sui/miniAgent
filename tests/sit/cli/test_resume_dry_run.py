import hashlib
import os
from uuid import uuid4

from tests.support.cli.resume_dry_run import invoke


async def test_real_dry_run_is_read_only_without_provider_key(resume_case, tmp_path):
    application, _execution, provider = resume_case
    deps = application.dependencies
    before = deps.run_store.path.read_bytes()
    record = await deps.run_store.get(deps.run_id)
    artifact_root = deps.artifact_store.root
    assert not artifact_root.exists()
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment["OPENAI_BASE_URL"] = "https://ignored.example/secret-resume-fixture"
    code, output = await invoke(
        deps.run_store.path,
        deps.run_id,
        "--dry-run",
        environment=environment,
        cwd=tmp_path,
    )
    assert code == 0, output
    assert "ready" in output and str(deps.run_id) in output
    assert "secret-resume-fixture" not in output
    assert (
        hashlib.sha256(deps.run_store.path.read_bytes()).digest()
        == hashlib.sha256(before).digest()
    )
    assert await deps.run_store.get(deps.run_id) == record
    assert not artifact_root.exists() and provider.requests == []


async def test_missing_database_dry_run_does_not_create_it(tmp_path):
    database = tmp_path / "missing" / "runs.sqlite3"
    code, output = await invoke(database, uuid4(), "--dry-run")
    assert code == 1 and "RUN_NOT_FOUND" in output
    assert not database.exists()


async def test_lock_conflict_precedes_missing_run(tmp_path):
    from miniagent.storage.run_lock import RunLock

    database = tmp_path / "missing.sqlite3"
    run_id = uuid4()
    with RunLock(database, run_id):
        code, output = await invoke(database, run_id, "--dry-run")
    assert code == 1 and "RUN_IN_USE" in output
    assert not database.exists()


async def test_unknown_shell_precedes_missing_key_and_retains_scene(resume_case):
    from miniagent.models.messages import AssistantMessage
    from miniagent.models.protocol import FinishReason, ModelResponse
    from miniagent.runtime.progress import ToolsDisposition
    from miniagent.tools.contracts import ToolCall

    application, execution, provider = resume_case
    deps = application.dependencies
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(
                tool_calls=(
                    ToolCall(
                        id="shell-call",
                        name="shell",
                        arguments={"command": "echo unknown"},
                    ),
                )
            ),
            finish_reason=FinishReason.TOOL_CALL,
        ),
        None,
        disposition=ToolsDisposition(),
    )
    await execution.begin_tool(
        call_index=0, tool_call_id="shell-call", tool_name="shell"
    )
    await execution.begin_tool_execution()
    before = await deps.run_store.get(deps.run_id)
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    code, output = await invoke(
        deps.run_store.path, deps.run_id, "--dry-run", environment=environment
    )
    assert code == 1 and "UNCERTAIN_TOOL_EXECUTION" in output
    assert "shell" in output and "Step 1, call 0" in output
    assert "retained" in output and "force" not in output
    assert await deps.run_store.get(deps.run_id) == before
    assert provider.requests == []


async def test_old_database_is_not_initialized_or_migrated(tmp_path):
    import sqlite3

    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("create table old_data(value text)")
    before = database.read_bytes()
    code, output = await invoke(database, uuid4(), "--dry-run")
    assert code == 1 and "RESUME_SCHEMA_UNSUPPORTED" in output
    assert database.read_bytes() == before
