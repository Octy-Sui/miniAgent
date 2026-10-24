from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from miniagent.cli.eval_batch import TaskResult, build_summary
from miniagent.cli.eval_metrics import (
    CallMeasurement,
    MetricsCollector,
    summarize_calls,
    summarize_models,
)
from miniagent.models.messages import UserMessage
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.events import (
    ModelRequested,
    ModelResponded,
    RunEvent,
    ToolEnded,
    ToolExecutionEnded,
    ToolExecutionStarted,
)
from miniagent.tools.contracts import ToolReturnValidation
from tests.support.runtime.runtime import final_response


def test_statistics_keep_precise_and_event_elapsed_samples_separate():
    calls = [
        CallMeasurement(
            attempt_id=uuid4(),
            kind="business",
            outcome="success",
            duration_ms=100,
            server_duration_ms=80,
            event_elapsed_ms=120,
        ),
        CallMeasurement(
            attempt_id=uuid4(), kind="business", outcome="failure", event_elapsed_ms=200
        ),
        CallMeasurement(attempt_id=uuid4(), kind="business", outcome="interrupted"),
        CallMeasurement(attempt_id=uuid4(), kind="business", outcome="unknown"),
    ]
    stats = summarize_calls(calls)
    assert stats.started == 4
    assert stats.succeeded == stats.failed == stats.interrupted == stats.unknown == 1
    assert stats.success_rate == 0.25
    assert stats.failure_rate == stats.interrupted_rate == stats.unknown_rate == 0.25
    assert stats.duration_ms.samples == 1
    assert stats.duration_ms.mean == stats.duration_ms.p95 == 100
    assert stats.event_elapsed_ms.samples == 2
    assert stats.event_elapsed_ms.mean == 160
    assert stats.server_duration_ms.mean == 80


def test_latency_percentiles_use_all_call_samples():
    calls = [
        CallMeasurement(
            attempt_id=uuid4(),
            kind="tool",
            name="shell",
            outcome="success",
            duration_ms=value,
        )
        for value in range(1, 21)
    ]
    stats = summarize_calls(calls)
    assert stats.duration_ms.samples == 20
    assert stats.duration_ms.total == 210
    assert stats.duration_ms.mean == 10.5
    assert stats.duration_ms.p50 == 10
    assert stats.duration_ms.p95 == 19
    assert stats.duration_ms.maximum == 20


def test_no_calls_has_no_success_rate_or_latency():
    stats = summarize_calls([])
    assert stats.started == 0
    assert stats.success_rate is None
    assert stats.failure_rate is stats.interrupted_rate is stats.unknown_rate is None
    assert stats.duration_ms.samples == 0
    assert stats.duration_ms.mean is None


def test_batch_percentiles_and_success_rate_merge_calls_instead_of_task_averages():
    slow = [
        CallMeasurement(
            attempt_id=uuid4(), kind="business", outcome="failure", duration_ms=100
        )
    ]
    fast = [
        CallMeasurement(
            attempt_id=uuid4(), kind="business", outcome="success", duration_ms=1
        )
        for _ in range(9)
    ]
    summary = build_summary(
        [
            TaskResult(instance_id="slow", model_metrics=summarize_models(slow)),
            TaskResult(instance_id="fast", model_metrics=summarize_models(fast)),
        ],
        "model",
    )
    assert summary.model_metrics.overall.success_rate == 0.9
    assert summary.model_metrics.overall.duration_ms.mean == 10.9
    assert summary.model_metrics.overall.duration_ms.p50 == 1
    assert summary.model_metrics.overall.duration_ms.p95 == 100


@pytest.mark.parametrize("legacy", [False, True])
def test_legacy_events_have_estimated_interval_without_fabricated_precise_rt(legacy):
    collector = MetricsCollector()
    run_id, attempt_id = uuid4(), None if legacy else uuid4()
    start = datetime(2026, 10, 22, tzinfo=UTC)
    collector.observe(
        RunEvent(
            run_id=run_id,
            sequence=1,
            occurred_at=start,
            payload=ModelRequested(
                request=ModelRequest(
                    model="test", messages=(UserMessage(content="issue"),)
                ),
                provider="test",
                model="test",
                attempt_id=attempt_id,
            ),
        )
    )
    collector.observe(
        RunEvent(
            run_id=run_id,
            sequence=2,
            occurred_at=start + timedelta(milliseconds=250),
            payload=ModelResponded(
                response=final_response(),
                finish_reason="stop",
                tool_call_count=0,
                attempt_id=attempt_id,
            ),
        )
    )
    stats = collector.models().overall
    assert stats.succeeded == 1
    assert stats.duration_ms.samples == 0
    assert stats.duration_ms.mean is None
    assert stats.event_elapsed_ms.samples == 1
    assert stats.event_elapsed_ms.mean == 250


def test_cancelled_execution_is_not_counted_as_unexecuted():
    collector = MetricsCollector()
    run_id, attempt_id = uuid4(), uuid4()
    payloads = [
        ToolExecutionStarted(
            attempt_id=attempt_id, call_index=0, tool_call_id="call", tool_name="shell"
        ),
        ToolExecutionEnded(
            attempt_id=attempt_id,
            status="cancelled",
            call_index=0,
            tool_call_id="call",
            tool_name="shell",
        ),
        ToolEnded(
            raw_result=None,
            return_validation=ToolReturnValidation(
                disposition="not_returned", reason="run_terminated"
            ),
            response_error="cancelled",
            status="cancelled",
            call_index=0,
            tool_call_id="call",
            tool_name="shell",
        ),
    ]
    for sequence, payload in enumerate(payloads, 1):
        collector.observe(
            RunEvent(
                run_id=run_id,
                sequence=sequence,
                occurred_at=datetime.now(UTC),
                step_number=1,
                span_id="0123456789abcdef",
                payload=payload,
            )
        )
    assert collector.tools().overall.interrupted == 1
    assert collector.tools().not_executed == {}
