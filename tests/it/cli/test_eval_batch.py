import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from miniagent.artifacts import ArtifactWrite, FileArtifactStore
from miniagent.cli.eval_batch import collect_result, run_jobs
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.protocol import ModelProviderError, ModelRequestTiming
from miniagent.runtime import AgentRuntime, RuntimeConfig
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolError, ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.compaction import PipelineEstimator
from tests.support.runtime.context import summary_response
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    SuccessfulTool,
    agent,
    final_response,
    tool_response,
)


async def test_collects_journal_statistics_and_copies_exact_trajectory(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    content = b'{"kind":"test trajectory"}'
    await artifacts.put(
        ArtifactWrite(run_id=run.run_id, type="run_trajectory"), content
    )
    output = tmp_path / "output"
    output.mkdir()

    result = await collect_result("one", run.run_id, database, artifacts, output)

    assert result.passed is True
    assert result.model_requests == 1
    assert result.compaction_attempts == 0
    assert result.compactions == 0
    assert result.tool_executions == 0
    assert result.steps == 1
    assert result.cost_usd == run.usage.cost_usd
    assert result.trajectory is not None
    assert (output / result.trajectory).read_bytes() == content


async def test_missing_trajectory_keeps_statistics_and_reports_error(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.passed is True
    assert result.model_requests == 1
    assert result.trajectory is None
    assert result.error is not None
    assert "Trajectory" in result.error


async def test_statistics_include_summary_requests_and_actual_tool_executions(
    tmp_path: Path,
):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    summary = summary_response().model_copy(update={"usage": final_response().usage})
    run = await AgentRuntime(
        provider=QueueProvider([tool_response()] * 4 + [summary, final_response()]),
        tools=ToolRegistry([SuccessfulTool()]),
        policy_engine=AllowPolicyEngine(),
        run_store=store,
        artifact_store=artifacts,
        estimator=PipelineEstimator(),
        config=RuntimeConfig(context_window_tokens=12000),
        pricing=PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=Decimal("0.001"),
                    output_per_token=Decimal("0.002"),
                )
            ]
        ),
    ).run(agent(allowed_tools=["ok"]), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.model_requests == 6
    assert result.compaction_attempts == 1
    assert result.compactions == 1
    assert result.tool_executions == 4
    assert result.steps == 5
    assert result.cost_usd == Decimal("0.018")
    assert result.unconfirmed_model_requests == 0
    assert result.model_metrics is not None
    assert result.model_metrics.overall.started == 6
    assert result.model_metrics.overall.succeeded == 6
    assert result.model_metrics.compaction.succeeded == 1
    assert result.model_metrics.overall.duration_ms.samples == 6
    assert result.tool_metrics is not None
    assert result.tool_metrics.overall.success_rate == 1
    assert result.tool_metrics.by_tool["ok"].succeeded == 4
    assert result.run_duration_ms is not None


async def test_denied_calls_are_not_counted_as_tool_execution_failures(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([tool_response(), final_response()]),
        tools=ToolRegistry([SuccessfulTool()]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.tool_metrics is not None
    assert result.tool_metrics.overall.started == 0
    assert result.tool_metrics.overall.success_rate is None
    assert result.tool_metrics.not_executed == {"TOOL_NOT_ALLOWED": 1}


async def test_failed_model_call_preserves_precise_rt_in_eval(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    error = ModelProviderError(
        "failed",
        request_timing=ModelRequestTiming(
            client_duration_ms=1700,
            server_duration_ms=1613,
            provider_request_id="cloud-request",
        ),
    )
    run = await AgentRuntime(
        provider=QueueProvider([error]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.model_metrics is not None
    assert result.model_metrics.overall.failed == 1
    assert result.model_metrics.overall.interrupted == 0
    assert result.model_metrics.overall.success_rate == 0
    assert result.model_metrics.overall.duration_ms.mean == 1700
    assert result.model_metrics.overall.server_duration_ms.mean == 1613
    assert result.model_metrics.calls[0].provider_request_id == "cloud-request"


@pytest.mark.parametrize(
    "status", [ToolStatus.SUCCESS, ToolStatus.ERROR, ToolStatus.TIMEOUT]
)
async def test_tool_statistics_use_validated_status_and_execution_duration(
    tmp_path: Path, status: ToolStatus
):
    class TimedTool(SuccessfulTool):
        async def execute(self, arguments, *, tool_call_id):
            return ToolResult(
                tool_call_id=tool_call_id,
                status=status,
                content="result",
                duration_ms=123,
                error=None
                if status is ToolStatus.SUCCESS
                else ToolError(code="COMMAND_FAILED", message="failed"),
            )

    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([tool_response(), final_response()]),
        tools=ToolRegistry([TimedTool()]),
        policy_engine=AllowPolicyEngine(),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(allowed_tools=["ok"]), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.tool_metrics is not None
    stats = result.tool_metrics.by_tool["ok"]
    assert stats.started == 1
    assert stats.succeeded == (1 if status is ToolStatus.SUCCESS else 0)
    assert stats.failed == (0 if status is ToolStatus.SUCCESS else 1)
    assert stats.duration_ms.mean == stats.duration_ms.p95 == 123


async def test_missing_provider_usage_is_visible_in_task_cost(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([final_response().model_copy(update={"usage": None})]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    result = await collect_result("one", run.run_id, database, artifacts, tmp_path)
    assert result.model_requests == 1
    assert result.unconfirmed_model_requests == 1


async def test_trajectory_copy_failure_preserves_committed_statistics(tmp_path: Path):
    database = tmp_path / "runs.sqlite3"
    store = SQLiteRunStore(database)
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    run = await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), "issue")
    await artifacts.put(
        ArtifactWrite(run_id=run.run_id, type="run_trajectory"), b"trajectory"
    )
    result = await collect_result(
        "one", run.run_id, database, artifacts, tmp_path / "missing"
    )
    assert result.model_requests == 1
    assert result.passed is True
    assert result.trajectory is None
    assert result.error is not None
    assert "Trajectory" in result.error


async def test_process_launch_failure_does_not_abandon_other_jobs(tmp_path: Path):
    assert await run_jobs(
        [
            [str(tmp_path / "missing-executable")],
            [sys.executable, "-c", "print('completed')"],
        ],
        tmp_path,
        1,
    ) == [127, 0]
    assert "completed" in (tmp_path / "1.log").read_text()


async def test_cancellation_waits_for_active_workers_and_skips_queued_jobs(
    tmp_path: Path,
):
    worker = tmp_path / "interrupt.py"
    worker.write_text("""import signal, sys, time
from pathlib import Path
root = Path(sys.argv[1])
def stop(*args):
    (root / "stopped").write_text("yes")
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
(root / "started").write_text("yes")
time.sleep(30)
""")
    pending = asyncio.create_task(
        run_jobs(
            [
                [sys.executable, str(worker), str(tmp_path)],
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(tmp_path / 'queued')!r}).touch()",
                ],
            ],
            tmp_path,
            1,
        )
    )
    async with asyncio.timeout(5):
        while not (tmp_path / "started").exists():
            await asyncio.sleep(0.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (tmp_path / "stopped").read_text() == "yes"
    assert not (tmp_path / "queued").exists()


async def test_real_processes_respect_concurrency_and_continue_after_failure(
    tmp_path: Path,
):
    worker = tmp_path / "worker.py"
    worker.write_text("""import json, sys, time
from pathlib import Path
root = Path(sys.argv[1])
index = sys.argv[2]
(root / (index + ".start")).write_text(str(time.monotonic_ns()))
time.sleep(0.15)
(root / (index + ".end")).write_text(str(time.monotonic_ns()))
print("worker", index)
sys.exit(1 if index == "1" else 0)
""")
    commands = [[sys.executable, str(worker), str(tmp_path), str(i)] for i in range(7)]
    assert await run_jobs(commands, tmp_path, 3) == [0, 1, 0, 0, 0, 0, 0]
    events = []
    for i in range(7):
        events.extend(
            [
                (int((tmp_path / f"{i}.start").read_text()), 1),
                (int((tmp_path / f"{i}.end").read_text()), -1),
            ]
        )
    active = peak = 0
    for _, delta in sorted(events):
        active += delta
        peak = max(peak, active)
    assert peak == 3
    assert active == 0
    assert "worker 6" in (tmp_path / "6.log").read_text()


@pytest.mark.parametrize("limit", [0, 4])
async def test_rejects_invalid_concurrency_before_launch(tmp_path: Path, limit: int):
    with pytest.raises(ValueError, match="1.*3"):
        await run_jobs([], tmp_path, limit)
