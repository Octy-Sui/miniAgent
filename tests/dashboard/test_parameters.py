from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from miniagent.dashboard.parameters import RunFilters


def test_run_filters_normalize_without_changing_literal_task() -> None:
    filters = RunFilters(
        statuses=("running", "created", "running"),
        task="literal % done",
        created_from=datetime(
            2026, 10, 19, 8, tzinfo=__import__("datetime").timezone(timedelta(hours=8))
        ),
    )
    assert filters.statuses == ("created", "running")
    assert filters.task == "literal % done"
    assert filters.created_from == datetime(2026, 10, 19, tzinfo=UTC)


@pytest.mark.parametrize(
    ("values",),
    [
        ({"created_from": "2026-10-19T00:00:00"},),
        (
            {
                "created_from": "2026-10-19T00:00:00Z",
                "created_to": "2026-10-19T00:00:00Z",
            },
        ),
        ({"unexpected": "secret-value"},),
        ({"task": "中" * 201},),
    ],
)
def test_run_filters_reject_invalid_ranges(values) -> None:
    with pytest.raises(ValidationError):
        RunFilters.model_validate(values)


from miniagent.dashboard.parameters import (
    ArtifactParams,
    EventFilters,
    EventParams,
    RunListParams,
    SnapshotParams,
)


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (EventFilters, {"step": 0}),
        (EventFilters, {"types": ["unknown"]}),
        (EventParams, {"filters": {}, "limit": 0}),
        (EventParams, {"filters": {}, "limit": 201}),
        (
            EventParams,
            {
                "filters": {},
                "cursor": "x",
                "event_id": "00000000-0000-0000-0000-000000000001",
            },
        ),
        (SnapshotParams, {"boundary": "x", "start": -1}),
        (SnapshotParams, {"boundary": "x", "start": 10, "end": 2}),
        (ArtifactParams, {"boundary": "x", "offset": -1}),
        (RunListParams, {"filters": {}, "page_size": 11}),
    ],
)
def test_parameter_ranges(model, values) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(values)


def test_http_parameters_preserve_multiselect_and_literal_search() -> None:
    from miniagent.dashboard.parameters import run_parameters

    params = run_parameters(
        [
            ("status", "running"),
            ("status", "created"),
            ("status", "running"),
            ("task", "literal % done"),
            ("page_size", "10"),
        ]
    )
    assert params.filters.statuses == ("created", "running")
    assert params.filters.task == "literal % done"
    assert params.page_size == 10


@pytest.mark.parametrize(
    ("pairs",),
    [
        ([("model", "one"), ("model", "two")],),
        ([("unknown", "secret")],),
        ([("page_size", "not-int")],),
        ([("page_size", "11")],),
    ],
)
def test_http_parameters_reject_ambiguous_single_values(pairs) -> None:
    from miniagent.dashboard.parameters import run_parameters
    from miniagent.dashboard.reads import ReadFailure

    with pytest.raises(ReadFailure):
        run_parameters(pairs)


def test_http_event_parameters_validate_selection() -> None:
    from miniagent.dashboard.parameters import event_parameters

    params = event_parameters(
        [
            ("type", "tool_end"),
            ("type", "step_completed"),
            ("step", "2"),
            ("limit", "200"),
            ("failures_only", "true"),
        ]
    )
    assert params.filters.types == ("step_completed", "tool_end")
    assert params.filters.step == 2
    assert params.filters.failures_only
    assert params.limit == 200
