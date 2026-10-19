from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.projections.run import format_usd, project_run
from miniagent.dashboard.queries.runs import read_run_page
from miniagent.dashboard.reads import ReadBudget, ReadIssue
from miniagent.metering.usage import RunUsage
from tests.dashboard.builders import make_run


async def recorded_run(database: Path):
    run_id = await make_run(database, 1)
    return read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )


async def test_usage_uses_persisted_totals(database: Path) -> None:
    page = await recorded_run(database)
    assert page.run.record is not None
    usage = RunUsage(
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cached_input_tokens=30,
        reasoning_output_tokens=5,
        cost_usd=Decimal("0.30"),
        input_cost_usd=Decimal("0.20"),
        output_cost_usd=Decimal("0.10"),
        active_duration_ms=12000,
    )
    state = page.run.record.state.model_copy(update={"usage": usage})
    run = replace(page.run, record=page.run.record.model_copy(update={"state": state}))
    fields = {field.key: field for field in project_run(run, page.boundary).usage}
    assert fields["total_tokens"].value == "120"
    assert fields["cost_usd"].value == "0.300000"
    assert fields["active_duration_ms"].value == "12000"
    assert fields["cost_recorded_decimal"].value == "0.30"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("0.0000005"), "0.000000"), (Decimal("0.0000015"), "0.000002")],
)
def test_currency_rounding(value: Decimal, expected: str) -> None:
    assert format_usd(value) == expected


async def test_unknown_evaluator_is_not_inferred(database: Path) -> None:
    page = await recorded_run(database)
    view = project_run(replace(page.run, workspace_kind="local"), page.boundary)
    fields = {field.key: field.value for field in view.result}
    assert fields["evaluator_type"] == "该 Run 验证类型未记录，未证明独立验证"
    assert fields["evaluation"] == "未记录"
    assert fields["artifacts"] == "尚未检查，Workspace查看"


@pytest.mark.parametrize(
    ("steps", "expected"), [(9, "90%"), (10, "100%"), (11, "110%")]
)
async def test_wall_duration_and_step_limits(
    database: Path, steps: int, expected: str
) -> None:
    page = await recorded_run(database)
    assert page.run.record is not None
    state = page.run.record.state
    started = datetime(2026, 10, 19, tzinfo=UTC)
    state = state.model_copy(
        update={
            "lifecycle": state.lifecycle.model_copy(update={"started_at": started}),
            "execution": state.execution.model_copy(update={"steps_started": steps}),
            "definition": state.definition.model_copy(
                update={
                    "config": state.definition.config.model_copy(
                        update={"max_steps": 10}
                    )
                }
            ),
        }
    )
    run = replace(page.run, record=page.run.record.model_copy(update={"state": state}))
    view = project_run(
        run,
        page.boundary.model_copy(update={"read_at": started + timedelta(seconds=20)}),
    )
    fields = {field.key: field.value for field in (*view.budgets, *view.usage)}
    assert fields["wall_duration_ms"] == "20000"
    assert expected in fields["steps_started"]
    assert [fields[key] for key in ("time_limit", "token_limit", "cost_limit")] == [
        "未配置"
    ] * 3


async def test_missing_fields_override_pydantic_defaults(database: Path) -> None:
    page = await recorded_run(database)
    run = replace(
        page.run,
        issues=(
            ReadIssue(
                "field_not_recorded",
                "历史字段未记录",
                field_path=("usage", "total_tokens"),
            ),
        ),
    )
    fields = {field.key: field.value for field in project_run(run, page.boundary).usage}
    assert fields["total_tokens"] == "未记录"


async def test_oversized_run_preserves_envelope(database: Path) -> None:
    page = await recorded_run(database)
    view = project_run(
        replace(
            page.run,
            record=None,
            issues=(ReadIssue("read_limit_exceeded", "超出读取上限"),),
        ),
        page.boundary,
    )
    assert view.run_id == page.run.envelope.run_id
    assert view.status == "created"
    assert "未完成读取" in [field.value for field in view.usage]
    assert all(field.value != "0" for field in view.usage)
