from pathlib import Path

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.model_limits import ModelLimits, load_model_limits
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.state import RunDefinition


def test_default_qwen_limits() -> None:
    assert load_model_limits(Path('config/model_limits.toml'), 'qwen', 'qwen3.8-flash') == ModelLimits(context_window_tokens=32768, max_output_tokens=4096)


def test_selected_model_and_duplicate_or_missing_entries(tmp_path: Path) -> None:
    path = tmp_path / 'limits.toml'
    row = '[[models]]\nprovider="qwen"\nmodel="custom"\ncontext_window_tokens=12000\nmax_output_tokens=976\n'
    path.write_text(row)
    assert load_model_limits(path, 'qwen', 'custom').max_output_tokens == 976
    with pytest.raises(ValueError, match='missing'):
        load_model_limits(path, 'qwen', 'absent')
    path.write_text(row + row)
    with pytest.raises(ValueError, match='duplicate'):
        load_model_limits(path, 'qwen', 'custom')


@pytest.mark.parametrize(('output', 'reason'), [(10976, 'business input budget'), (9270, 'summary reserve')])
def test_independent_budget_rejections(output: int, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        RuntimeConfig(context_window_tokens=12000).validate_output_limit(output)


def test_summary_reserve_boundary() -> None:
    RuntimeConfig(context_window_tokens=12000).validate_output_limit(9269)


@pytest.mark.parametrize(('values',), [
    ({'context_window_tokens': 0},), ({'compaction_target_ratio': 0},),
    ({'compaction_target_ratio': .70},), ({'compaction_target_ratio': .75},),
    ({'compaction_trigger_ratio': 1},), ({'recent_steps_to_keep': 0},),
    ({'compaction_max_retries': -1},), ({'compaction_timeout_seconds': 0},),
])
def test_invalid_runtime_limits(values: dict[str, int | float]) -> None:
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(values)


def test_zero_output_rejected() -> None:
    with pytest.raises(ValueError):
        ModelLimits(context_window_tokens=12000, max_output_tokens=0)


def test_nondefaults_survive_definition_serialization() -> None:
    config = RuntimeConfig(context_window_tokens=12000, compaction_target_ratio=.4, compaction_trigger_ratio=.8, recent_steps_to_keep=2, compaction_timeout_seconds=7, compaction_max_retries=2)
    definition = RunDefinition(agent_spec=DEFAULT_AGENT, config=config)
    assert RunDefinition.model_validate_json(definition.model_dump_json()).config == config


async def test_invalid_limits_fail_before_workspace_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from miniagent.cli.bootstrap import BootstrapConfig, bootstrap

    path = tmp_path / 'limits.toml'
    path.write_text('[[models]]\nprovider="qwen"\nmodel="qwen3.8-flash"\ncontext_window_tokens=12000\nmax_output_tokens=10976\n')
    monkeypatch.setenv('MODEL_LIMITS_FILE', str(path))
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://example.com')
    monkeypatch.setenv('LOCAL_WORKSPACE_ROOT', str(tmp_path / 'workspaces'))
    with pytest.raises(ValueError, match='business input budget'):
        await bootstrap(BootstrapConfig(agent=DEFAULT_AGENT, task='task', source_dir=tmp_path / 'absent'))
    assert not (tmp_path / 'workspaces').exists()
