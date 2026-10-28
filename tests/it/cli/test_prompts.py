from pathlib import Path

import pytest
from typer.testing import CliRunner

from miniagent.artifacts import FileArtifactStore
from miniagent.cli.eval_batch import execute_batch
from miniagent.cli.main import app, parse_config
from miniagent.cli.prompts import load_prompts
from miniagent.evaluation.swebench_data import SWEbenchTaskContext
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.runtime import AgentRuntime
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import QueueProvider, final_response


def test_cli_reports_prompt_error_before_creating_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMPT_FILE", str(tmp_path / "missing.yaml"))
    database = tmp_path / "runs.sqlite3"
    monkeypatch.setenv("RUN_DATABASE_PATH", str(database))

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 1
    assert "Cannot load prompts" in invocation.stderr
    assert "missing.yaml" in invocation.stderr
    assert not database.exists()


def test_help_does_not_read_prompt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMPT_FILE", str(tmp_path / "missing.yaml"))
    assert CliRunner().invoke(app, ["--help"]).exit_code == 0
    assert CliRunner().invoke(app, ["resume", "--help"]).exit_code == 0


async def test_prompt_text_is_sent_to_provider_and_frozen_in_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "system_prompt: original\ninstance_prompt: 'Issue: {{ problem_statement }}'"
    )
    monkeypatch.setenv("PROMPT_FILE", str(path))
    config = parse_config(
        "bug {{ literal }}",
        None,
        None,
        None,
        swebench_task=SWEbenchTaskContext(instance_id="one"),
    )
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    provider = QueueProvider([final_response()])
    result = await AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    ).run(config.agent.model_copy(update={"allowed_tools": ()}), config.task)
    path.unlink()
    record = await store.get(result.run_id)
    assert record.state.definition.agent_spec.system_prompt == "original"
    assert record.state.identity.task == "Issue: bug {{ literal }}"
    assert provider.requests[0].messages[:2] == (
        SystemMessage(content="original"),
        UserMessage(content="Issue: bug {{ literal }}"),
    )


async def test_batch_snapshot_survives_original_prompt_file_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "system_prompt: frozen\ninstance_prompt: 'Issue: {{ problem_statement }}'"
    )
    monkeypatch.setenv("PROMPT_FILE", str(path))
    output = tmp_path / "batch"
    output.mkdir()
    await execute_batch([], "test-model", output, 1)
    path.unlink()
    config = parse_config(
        "bug",
        None,
        None,
        None,
        swebench_task=SWEbenchTaskContext(instance_id="one"),
        prompts=load_prompts(output / "prompts.yaml"),
    )
    assert config.agent.system_prompt == "frozen"
    assert config.task == "Issue: bug"
