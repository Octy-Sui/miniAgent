from pathlib import Path

import pytest

from miniagent.cli.main import parse_config
from miniagent.cli.prompts import PromptConfigError, load_prompts
from miniagent.evaluation.swebench_data import SWEbenchTaskContext


def test_loads_utf8_prompts_without_evaluating_task_text(tmp_path: Path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "system_prompt: |\n  中文\ninstance_prompt: 'Issue: {{ problem_statement }}'\n",
        encoding="utf-8",
    )
    prompts = load_prompts(path)
    assert prompts.system_prompt == "中文\n"
    assert prompts.render_instance("{{ unknown }}") == "Issue: {{ unknown }}"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("[invalid", "parsing"),
        ("[]", "dictionary"),
        ("system_prompt: x", "instance_prompt"),
        ("system_prompt: 1\ninstance_prompt: x", "system_prompt"),
        ("system_prompt: '  '\ninstance_prompt: x", "system_prompt"),
        ("system_prompt: x\ninstance_prompt: '  '", "instance_prompt"),
        ("system_prompt: x\ninstance_prompt: '\\n'\nextra: x", "extra"),
        ("system_prompt: x\ninstance_prompt: '{{ missing }}'", "missing"),
        (
            "system_prompt: x\ninstance_prompt: '{% if problem_statement %}{{ misspelled }}{% endif %}'",
            "misspelled",
        ),
        ("system_prompt: x\ninstance_prompt: '{{'", "unexpected"),
    ],
)
def test_rejects_invalid_prompts_with_file_context(
    tmp_path: Path, content: str, reason: str
) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PromptConfigError, match=reason) as error:
        load_prompts(path)
    assert str(path) in str(error.value)


def test_missing_prompt_file_has_file_context(tmp_path: Path) -> None:
    path = tmp_path / "missing.yaml"
    with pytest.raises(PromptConfigError, match="missing.yaml"):
        load_prompts(path)


def test_task_dependent_render_error_has_file_context(tmp_path: Path) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "system_prompt: x\ninstance_prompt: '{% if problem_statement %}{{ 1 / 0 }}{% endif %}'"
    )
    prompts = load_prompts(path)
    with pytest.raises(PromptConfigError, match="division by zero") as error:
        prompts.render_instance("issue")
    assert str(path) in str(error.value)


def test_new_runs_reload_prompts_and_keep_raw_cli_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "prompts.yaml"
    monkeypatch.setenv("PROMPT_FILE", str(path))
    path.write_text(
        "system_prompt: first\ninstance_prompt: 'Issue: {{ problem_statement }}'"
    )
    first = parse_config("raw task", None, None, None)
    path.write_text(
        "system_prompt: second\ninstance_prompt: 'Issue: {{ problem_statement }}'"
    )
    second = parse_config("raw task", None, None, None)
    assert first.agent.system_prompt == "first"
    assert second.agent.system_prompt == "second"
    assert first.task == second.task == "raw task"


def test_swebench_composition_renders_instance_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "system_prompt: custom\ninstance_prompt: 'Issue: {{ problem_statement }}'"
    )
    monkeypatch.setenv("PROMPT_FILE", str(path))
    config = parse_config(
        "bug {{ untouched }}",
        "replacement",
        None,
        None,
        swebench_task=SWEbenchTaskContext(instance_id="one"),
    )
    assert config.task == "Issue: bug {{ untouched }}"
    assert config.agent.system_prompt == "custom"
    assert config.agent.model.model == "replacement"
