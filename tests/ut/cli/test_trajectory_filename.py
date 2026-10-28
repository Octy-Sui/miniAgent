from datetime import UTC, datetime

import pytest

from miniagent.cli import trajectory
from miniagent.runtime.config import RuntimeConfig
from miniagent.trajectory import TrajectoryDefinition
from tests.support.cli.agent import DEFAULT_AGENT


@pytest.mark.parametrize(
    ("task", "model", "instance_id", "expected"),
    [
        ("fix bug\nmore details", "qwen3.8-flash", None, "fix_bug_qwen3.8-flash"),
        ("任务：修复/错误", "org/model:v1", None, "任务_修复_错误_org_model_v1"),
        ('../\\:*?"<>|', "...", None, "task_model"),
        ("", "model", None, "task_model"),
        (
            "Reproduce the problem\nlong description",
            "model",
            "django__django-11477",
            "django__django-11477_model",
        ),
    ],
)
def test_filename_uses_task_model_and_execution_date(
    task, model, instance_id, expected
):
    definition = TrajectoryDefinition(
        task=task,
        agent=DEFAULT_AGENT.model_copy(
            update={"model": DEFAULT_AGENT.model.model_copy(update={"model": model})}
        ),
        budgets=RuntimeConfig(),
        benchmark=(
            {"instance_id": instance_id, "task_digest": "a" * 64}
            if instance_id
            else None
        ),
    )
    assert (
        trajectory.trajectory_filename(
            definition, datetime(2026, 10, 20, 23, 59, tzinfo=UTC)
        )
        == f"{expected}_2026-10-20.json"
    )


def test_long_unicode_names_fit_a_filesystem_segment():
    definition = TrajectoryDefinition(
        task="任务" * 1000,
        agent=DEFAULT_AGENT.model_copy(
            update={
                "model": DEFAULT_AGENT.model.model_copy(update={"model": "模型" * 1000})
            }
        ),
        budgets=RuntimeConfig(),
    )
    name = trajectory.trajectory_filename(
        definition, datetime(2026, 10, 20, tzinfo=UTC)
    )
    assert len(name.encode("utf-8")) <= 255
    assert name.startswith("任务")
    assert name.endswith("_2026-10-20.json")
