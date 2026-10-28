import pytest

from miniagent.cli.model_limits import ModelLimits
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.state import RunDefinition
from tests.support.cli.agent import DEFAULT_AGENT


@pytest.mark.parametrize(
    ("output", "reason"), [(10976, "business input budget"), (9270, "summary reserve")]
)
def test_independent_budget_rejections(output: int, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        RuntimeConfig(context_window_tokens=12000).validate_output_limit(output)


def test_summary_reserve_boundary() -> None:
    RuntimeConfig(context_window_tokens=12000).validate_output_limit(9269)


@pytest.mark.parametrize(
    ("values",),
    [
        ({"context_window_tokens": 0},),
        ({"compaction_target_ratio": 0},),
        ({"compaction_target_ratio": 0.70},),
        ({"compaction_target_ratio": 0.75},),
        ({"compaction_trigger_ratio": 1},),
        ({"recent_steps_to_keep": 0},),
        ({"compaction_max_retries": -1},),
        ({"compaction_timeout_seconds": 0},),
    ],
)
def test_invalid_runtime_limits(values: dict[str, int | float]) -> None:
    with pytest.raises(ValueError):
        RuntimeConfig.model_validate(values)


def test_zero_output_rejected() -> None:
    with pytest.raises(ValueError):
        ModelLimits(context_window_tokens=12000, max_output_tokens=0)


def test_nondefaults_survive_definition_serialization() -> None:
    config = RuntimeConfig(
        context_window_tokens=12000,
        compaction_target_ratio=0.4,
        compaction_trigger_ratio=0.8,
        recent_steps_to_keep=2,
        compaction_timeout_seconds=7,
        compaction_max_retries=2,
    )
    definition = RunDefinition(agent_spec=DEFAULT_AGENT, config=config)
    assert (
        RunDefinition.model_validate_json(definition.model_dump_json()).config == config
    )
