import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.dashboard.parameters import RunFilters
from miniagent.dashboard.queries.runs import list_runs
from miniagent.dashboard.reads import ReadBudget
from tests.support.dashboard.builders import make_run
from tests.support.dashboard.observation import capture_read_only


def budget() -> ReadBudget:
    return ReadBudget(5, monotonic=lambda: 0)


async def test_list_keyset_survives_new_insert(database: Path) -> None:
    for number in range(1, 31):
        await make_run(database, number)
    page = list_runs(database, RunFilters(), page_size=10, budget=budget())
    seen = [item.envelope.run_id for item in page.items]
    await make_run(database, 99, created_at=datetime(2026, 10, 20, tzinfo=UTC))
    while page.next_position is not None:
        page = list_runs(
            database,
            RunFilters(),
            page_size=10,
            after=page.next_position,
            budget=budget(),
        )
        seen.extend(item.envelope.run_id for item in page.items)
    assert seen == [
        UUID(int=n)
        for n in [
            30,
            29,
            28,
            27,
            26,
            25,
            24,
            23,
            22,
            21,
            20,
            19,
            18,
            17,
            16,
            15,
            14,
            13,
            12,
            11,
            10,
            9,
            8,
            7,
            6,
            5,
            4,
            3,
            2,
            1,
        ]
    ]


async def test_literal_task_filters_and_counts(database: Path) -> None:
    expected = await make_run(database, 1, task="literal % done")
    await make_run(database, 2, task="literal many done")
    result = list_runs(database, RunFilters(task="%"), budget=budget())
    assert [item.envelope.run_id for item in result.items] == [expected]
    assert result.total == sum(result.counts.values()) == 1
    assert result.items[0].cost_usd == 0
    assert result.items[0].workspace_kind == "unknown"
    assert result.items[0].model == "test-model"


async def test_oversized_run_preserves_envelope(database: Path) -> None:
    run_id = await make_run(database, 1, task="z" * (20 * 1024 * 1024 + 1))
    result = list_runs(database, RunFilters(), budget=budget())
    assert result.items[0].envelope.run_id == run_id
    assert result.items[0].envelope.status == "created"
    assert "read_limit_exceeded" in {issue.code for issue in result.items[0].issues}
    assert result.items[0].task is None
    assert result.items[0].cost_usd is None


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("identity.run_id", "00000000-0000-0000-0000-000000000099"),
        ("lifecycle.status", "running"),
        ("identity.created_at", "2025-01-01T00:00:00Z"),
    ],
)
async def test_inconsistent_identity_is_not_a_model_match(
    database: Path, field: str, replacement: str
) -> None:
    await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_set(state_json,?,?)",
            ("$." + field, replacement),
        )
        connection.commit()
    result = list_runs(database, RunFilters(), budget=budget())
    assert "run_inconsistent" in {issue.code for issue in result.items[0].issues}
    assert (
        list_runs(database, RunFilters(model="test-model"), budget=budget()).total == 0
    )


@pytest.mark.parametrize(
    ("length",), [(131070,), (131071,), (131072,), (131073,), (131074,)]
)
async def test_task_byte_gate(database: Path, length: int) -> None:
    await make_run(database, 1, task="x" * length)
    item = list_runs(database, RunFilters(), budget=budget()).items[0]
    if length <= 131072:
        assert item.task == "x" * length
    else:
        assert item.task is None
        assert "read_limit_exceeded" in {issue.code for issue in item.issues}


async def test_filters_and_scalar_sql_preserve_business_data(
    database: Path, tmp_path: Path
) -> None:
    first = await make_run(database, 1, task="literal _ \\")
    await make_run(database, 2, created_at=datetime(2026, 10, 20, tzinfo=UTC))
    before = capture_read_only(database, tmp_path / "artifacts")
    statements = []

    def connect(*args, **kwargs):
        connection = sqlite3.connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    result = list_runs(
        database,
        RunFilters(
            statuses=("created",),
            model="test-model",
            workspace="unknown",
            created_from=datetime(2026, 10, 19, tzinfo=UTC),
            created_to=datetime(2026, 10, 20, tzinfo=UTC),
            task="_ \\",
        ),
        budget=budget(),
        connect=connect,
    )
    assert [item.envelope.run_id for item in result.items] == [first]
    assert (
        list_runs(database, RunFilters(model="missing-model"), budget=budget()).total
        == 0
    )
    assert capture_read_only(database, tmp_path / "artifacts") == before
    assert not any(
        "run_events" in statement or "run_snapshots" in statement
        for statement in statements
    )


async def test_missing_usage_is_not_zero(database: Path) -> None:
    await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_remove(state_json,'$.usage.cost_usd','$.usage.active_duration_ms')"
        )
        connection.commit()
    item = list_runs(database, RunFilters(), budget=budget()).items[0]
    assert item.cost_usd is None
    assert item.active_duration_ms is None


@pytest.mark.parametrize(("length",), [(4094,), (4095,), (4096,), (4097,), (4098,)])
async def test_model_scalar_gate(database: Path, length: int) -> None:
    await make_run(database, 1)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET state_json=json_set(state_json,'$.definition.agent_spec.model.model',?)",
            ("m" * length,),
        )
        connection.commit()
    item = list_runs(database, RunFilters(), budget=budget()).items[0]
    assert item.model == ("m" * length if length <= 4096 else None)
    if length > 4096:
        assert "read_limit_exceeded" in {issue.code for issue in item.issues}


async def test_workspace_and_decimal_use_persisted_values(
    database: Path, tmp_path: Path
) -> None:
    from decimal import Decimal

    from miniagent.environments.config import LocalOptions
    from miniagent.environments.persistence import PersistedLocalWorkspaceSpec
    from miniagent.runtime.recovery import RunRecoveryContext

    run_id = await make_run(database, 1)
    context = RunRecoveryContext(
        workspace_id="persisted-id",
        workspace_spec=PersistedLocalWorkspaceSpec(
            run_id=run_id, options=LocalOptions(source_dir=tmp_path)
        ),
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "UPDATE runs SET recovery_context_json=?,state_json=json_set(state_json,'$.usage.cost_usd',?)",
            (context.model_dump_json(), "0.123456789123456789"),
        )
        connection.commit()
    result = list_runs(database, RunFilters(workspace="local"), budget=budget())
    assert result.total == 1
    assert result.items[0].cost_usd == Decimal("0.123456789123456789")
    assert result.items[0].workspace_kind == "local"


async def test_oversized_recovery_is_unknown_not_missing_run(database: Path) -> None:
    await make_run(database, 1)
    raw = json.dumps(
        {"workspace_spec": {"kind": "docker"}, "padding": "x" * (1024 * 1024)}
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE runs SET recovery_context_json=?", (raw,))
        connection.commit()
    item = list_runs(database, RunFilters(), budget=budget()).items[0]
    assert item.workspace_kind == "unknown"
    assert item.task == "visible task"
    assert "read_limit_exceeded" in {issue.code for issue in item.issues}
