from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from miniagent.cli.eval_batch import (
    EvalTask,
    TaskResult,
    build_summary,
    create_output_directory,
    select_tasks,
)


def task(
    name: str, selection: Literal["development", "evaluation"] = "evaluation"
) -> EvalTask:
    return EvalTask(
        instance_id=name,
        selection=selection,
        problem_statement="issue",
        image="docker.io/swebench/test@sha256:" + "a" * 64,
        platform="linux/amd64",
    )


def test_selects_all_evaluation_tasks_only():
    tasks = [task("dev", "development"), task("one"), task("two")]
    assert [t.instance_id for t in select_tasks(tasks, "all", [])] == ["one", "two"]


def test_multiple_selection_preserves_order_and_deduplicates():
    assert [
        t.instance_id
        for t in select_tasks([task("one"), task("two")], None, ["two", "one", "two"])
    ] == ["two", "one"]


@pytest.mark.parametrize(
    ("target", "ids", "message"),
    [
        (None, [], "Select"),
        ("all", ["one"], "combine"),
        ("other", [], "all"),
        (None, ["missing"], "Unknown"),
        (None, ["dev"], "evaluation"),
    ],
)
def test_rejects_invalid_selection(target, ids, message):
    with pytest.raises(ValueError, match=message):
        select_tasks([task("one"), task("dev", "development")], target, ids)


def test_same_minute_batches_never_overwrite(tmp_path: Path):
    now = datetime(2026, 10, 22, 16, 30, tzinfo=UTC)
    first = create_output_directory(tmp_path, now)
    (first / "trajectory.json").write_text("keep")
    second = create_output_directory(tmp_path, now)
    assert first.name == "2026-10-22_16-30"
    assert second != first
    assert (first / "trajectory.json").read_text() == "keep"


def test_summary_counts_errors_in_denominator_and_preserves_unknown_cost():
    results = [
        TaskResult(
            instance_id="one",
            status="succeeded",
            passed=True,
            cost_usd=Decimal("0.12"),
            model_requests=4,
            compaction_attempts=2,
            compactions=1,
            tool_executions=3,
            steps=2,
        ),
        TaskResult(instance_id="two", status="error", error="preparation failed"),
    ]
    summary = build_summary(results, "test-model")
    assert summary.pass_rate == 0.5
    assert summary.passed == 1
    assert summary.total == 2
    assert summary.known_cost_usd == Decimal("0.12")
    assert summary.unknown_cost_tasks == 1
    assert summary.model_requests == 4
    assert summary.tasks[1].cost_usd is None


def test_unconfirmed_usage_marks_cost_as_incomplete():
    result = TaskResult(
        instance_id="one", cost_usd=Decimal("0.12"), unconfirmed_model_requests=1
    )
    summary = build_summary([result], "test-model")
    assert summary.known_cost_usd == Decimal("0.12")
    assert summary.unknown_cost_tasks == 1
    assert summary.tasks[0].unconfirmed_model_requests == 1
