import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.projections.spans import project_spans
from miniagent.dashboard.queries.events import read_scope_facts
from miniagent.dashboard.reads import ReadBudget
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.events import (
    ApprovalRequested,
    ApprovalResolved,
    EvaluationCompleted,
    EvaluationStart,
    ModelRequested,
    ModelResponded,
    NewRunEvent,
    RunFinished,
    RunStarted,
    StepCompleted,
    StepStarted,
    ToolEnded,
    ToolExecutionEnded,
    ToolExecutionStarted,
    ToolStarted,
)
from miniagent.tools.contracts import ToolReturnValidation
from tests.support.dashboard.builders import make_run


async def span_history(database: Path) -> tuple[PageBoundary, dict[str, str]]:
    run_id = await make_run(database, 1)
    ids = {
        kind: f"{number:016x}"
        for number, kind in enumerate(
            (
                "run",
                "step",
                "model",
                "tool",
                "tool_execution",
                "evaluation",
                "approval_wait",
            ),
            1,
        )
    }

    pairs = [
        (
            "run",
            None,
            RunStarted(),
            RunFinished(status="succeeded", reason="task_verified"),
        ),
        ("step", "run", StepStarted(), StepCompleted(failed=False)),
        (
            "model",
            "step",
            ModelRequested(
                provider="test",
                model="test",
                request=ModelRequest(model="test", messages=()),
            ),
            ModelResponded(finish_reason="stop", tool_call_count=0),
        ),
        (
            "tool",
            "step",
            ToolStarted(
                call_index=0, tool_call_id="call", tool_name="shell", arguments={}
            ),
            ToolEnded(
                call_index=0,
                tool_call_id="call",
                tool_name="shell",
                status="success",
                raw_result=None,
                return_validation=ToolReturnValidation(
                    disposition="not_returned", reason="fixture_not_returned"
                ),
                observation=None,
                observation_missing_reason="fixture_not_observed",
            ),
        ),
        (
            "tool_execution",
            "tool",
            ToolExecutionStarted(
                call_index=0,
                tool_call_id="call",
                tool_name="shell",
                attempt_id=UUID(int=10),
            ),
            ToolExecutionEnded(
                call_index=0,
                tool_call_id="call",
                tool_name="shell",
                attempt_id=UUID(int=10),
                status="success",
            ),
        ),
        ("evaluation", "run", EvaluationStart(), EvaluationCompleted(passed=True)),
        (
            "approval_wait",
            "tool",
            ApprovalRequested(
                call_index=0,
                tool_call_id="call",
                tool_name="shell",
                approval_id=UUID(int=11),
                decision_id=UUID(int=12),
            ),
            ApprovalResolved(
                call_index=0,
                tool_call_id="call",
                tool_name="shell",
                approval_id=UUID(int=11),
                outcome="approved",
            ),
        ),
    ]
    rows = []
    for kind, parent, start, end in pairs:
        for offset, payload in enumerate((start, end)):
            event = NewRunEvent(
                run_id=run_id,
                payload=payload,
                span_id=ids[kind],
                parent_span_id=ids[parent] if parent else None,
                step_number=1
                if kind in ("step", "tool", "tool_execution", "approval_wait")
                else None,
            )
            rows.append(
                (
                    str(run_id),
                    len(rows) + 1,
                    str(event.event_id),
                    str(event.type),
                    f"2026-10-19T00:00:0{offset * 2}Z",
                    event.payload.model_dump_json(),
                    event.step_number,
                    event.span_id,
                    event.parent_span_id,
                )
            )
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,step_number,span_id,parent_span_id) VALUES(?,?,?,3,?,?,?,'[]',?,?,?)",
            rows,
        )
        connection.execute("UPDATE runs SET last_event_sequence=14")
        connection.commit()
    return PageBoundary(
        run_id=run_id,
        as_of_sequence=14,
        latest_snapshot_version=0,
        run_status="succeeded",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    ), ids


@pytest.mark.parametrize(
    ("kind",),
    [
        (kind,)
        for kind in (
            "run",
            "step",
            "model",
            "tool",
            "tool_execution",
            "evaluation",
            "approval_wait",
        )
    ],
)
async def test_scope_pairs_survive_filters_and_pages(database: Path, kind: str) -> None:
    boundary, ids = await span_history(database)
    facts = read_scope_facts(
        database, boundary, (ids[kind],), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    views = {view.span_id: view for view in project_spans(facts, boundary)}
    assert views[ids[kind]].duration_ms == 2000
    assert views[ids[kind]].category == kind
    assert views[ids[kind]].issues == ()


async def test_span_issues_preserve_events(database: Path) -> None:
    boundary, ids = await span_history(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO run_events SELECT run_id,201,?,schema_version,type,occurred_at,payload_json,artifacts_json,step_number,span_id,parent_span_id FROM run_events WHERE type='step_started'",
            (str(UUID(int=999)),),
        )
        connection.execute("UPDATE runs SET last_event_sequence=201")
        connection.commit()
    boundary = boundary.model_copy(update={"as_of_sequence": 201})
    facts = read_scope_facts(
        database, boundary, (ids["step"],), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert facts.scopes[ids["step"]].start_count == 2
    view = next(
        view for view in project_spans(facts, boundary) if view.span_id == ids["step"]
    )
    assert view.duration_ms is None
    assert view.state == "duplicate"


async def test_unloaded_parent_is_not_missing(database: Path) -> None:
    boundary, ids = await span_history(database)
    facts = read_scope_facts(
        database,
        boundary,
        (ids["tool"],),
        budget=ReadBudget(5, remaining_bytes=0, monotonic=lambda: 0),
    )
    views = project_spans(facts, boundary)
    assert views
    assert any(issue.code == "not_loaded" for view in views for issue in view.issues)
    assert not any(
        issue.code == "missing_parent" for view in views for issue in view.issues
    )


async def test_boundary_status_alone_controls_open_scope(database: Path) -> None:
    run_id = await make_run(database, 1)
    payload = RunStarted()
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,span_id) VALUES(?,1,?,3,'run_started','2026-10-19T00:00:00Z',?,'[]','0000000000000001')",
            (str(run_id), str(UUID(int=20)), payload.model_dump_json()),
        )
        connection.execute("UPDATE runs SET last_event_sequence=1")
        connection.commit()
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=1,
        latest_snapshot_version=0,
        run_status="running",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    facts = read_scope_facts(
        database,
        boundary,
        ("0000000000000001",),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert project_spans(facts, boundary)[0].state == "open"
    assert (
        project_spans(facts, boundary.model_copy(update={"run_status": "succeeded"}))[
            0
        ].state
        == "incomplete"
    )
    assert project_spans(facts, boundary)[0].duration_ms is None


@pytest.mark.parametrize(("count",), [(62,), (63,), (64,), (65,), (66,)])
async def test_abnormal_parent_chain_is_bounded(database: Path, count: int) -> None:
    run_id = await make_run(database, 1)
    rows = []
    for n in range(1, count + 1):
        payload = ModelRequested(
            provider="test",
            model="test",
            request=ModelRequest(model="test", messages=()),
        )
        rows.append(
            (
                str(run_id),
                n,
                str(UUID(int=n + 100)),
                str(payload.type),
                payload.model_dump_json(),
                f"{n:016x}",
                f"{n + 1:016x}",
            )
        )
    root = RunStarted()
    rows.append(
        (
            str(run_id),
            count + 1,
            str(UUID(int=count + 101)),
            str(root.type),
            root.model_dump_json(),
            f"{count + 1:016x}",
            None,
        )
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,span_id,parent_span_id) VALUES(?,?,?,3,?,'2026-10-19T00:00:00Z',?,'[]',?,?)",
            rows,
        )
        connection.execute("UPDATE runs SET last_event_sequence=?", (count + 1,))
        connection.commit()
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=count + 1,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    facts = read_scope_facts(
        database,
        boundary,
        ("0000000000000001",),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert len(facts.scopes) == min(count + 1, 64)
    assert facts.complete == (count < 64)
    if count >= 64:
        issues = [
            issue.code
            for view in project_spans(facts, boundary)
            for issue in view.issues
        ]
        assert "not_loaded" in issues
        assert "missing_parent" not in issues


@pytest.mark.parametrize(("count",), [(998,), (999,), (1000,), (1001,), (1002,)])
async def test_scope_count_is_reachable_through_two_hundred_visible_ids(
    database: Path, count: int
) -> None:
    run_id = await make_run(database, 1)
    rows = []
    for n in range(1, count + 1):
        payload = (
            RunStarted()
            if n == count
            else ModelRequested(
                provider="test",
                model="test",
                request=ModelRequest(model="test", messages=()),
            )
        )
        parent = None if n == count else f"{min(n + 200, count):016x}"
        rows.append(
            (
                str(run_id),
                n,
                str(UUID(int=n + 100)),
                str(payload.type),
                payload.model_dump_json(),
                f"{n:016x}",
                parent,
            )
        )
    with closing(sqlite3.connect(database)) as connection:
        connection.executemany(
            "INSERT INTO run_events(run_id,sequence,event_id,schema_version,type,occurred_at,payload_json,artifacts_json,span_id,parent_span_id) VALUES(?,?,?,3,?,'2026-10-19T00:00:00Z',?,'[]',?,?)",
            rows,
        )
        connection.execute("UPDATE runs SET last_event_sequence=?", (count,))
        connection.commit()
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=count,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    facts = read_scope_facts(
        database,
        boundary,
        tuple(f"{n:016x}" for n in range(1, 201)),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert len(facts.scopes) == min(count, 1000)
    assert facts.complete == (count <= 1000)
    if count > 1000:
        assert "not_loaded" in {issue.code for issue in facts.issues}
        assert not any(
            issue.code == "missing_parent"
            for view in project_spans(facts, boundary)
            for issue in view.issues
        )
