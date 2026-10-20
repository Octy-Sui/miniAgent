from dataclasses import replace

from miniagent.runtime import AgentRuntime
from miniagent.runtime.context import select_compaction_prefix
from miniagent.runtime.events import (
    CompactionAttempt,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.result import RunError
from miniagent.runtime.state import RunLifecycle
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.compaction import PipelineEstimator
from tests.support.runtime.context import summary_plan
from tests.support.runtime.runtime import QueueProvider


async def failed_summary(tmp_path, used):
    plan = summary_plan()
    seed = plan.state.model_copy(
        update={
            "lifecycle": RunLifecycle(updated_at=plan.state.lifecycle.updated_at),
            "execution": plan.state.execution.model_copy(update={"cursor": None}),
            "definition": plan.state.definition.model_copy(
                update={
                    "config": plan.state.definition.config.model_copy(
                        update={"compaction_max_retries": 2}
                    )
                }
            ),
        }
    )
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    execution = await RunExecution.start(
        seed,
        RunJournal(
            RunRecorder(run_id=seed.identity.run_id, store=store, recovery_context=None)
        ),
    )
    await execution.begin_step(provider="qwen", model="test")
    await execution.record_model_failure("CONTEXT_CAPACITY_EXCEEDED")
    runtime = AgentRuntime(
        provider=QueueProvider([]),
        tools=ToolRegistry([]),
        run_store=store,
        estimator=PipelineEstimator(),
    )
    plan = select_compaction_prefix(
        execution.state,
        runtime._build_request(execution.state),
        runtime.estimator,
        force=True,
    )
    source = await store.load_latest(seed.identity.run_id)
    assert plan is not None and source is not None
    plan = replace(
        plan,
        source_snapshot_version=source.version,
        source_event_sequence=source.event_sequence,
    )
    for number in range(used + 1):
        await execution.begin_compaction_attempt(
            plan, number + 1, branch="forced_compaction"
        )
        await execution.record_compaction_attempt(
            CompactionAttempt(attempt=number + 1, error_code="RETRY"),
            failure=RunError(code="RETRY", message="retryable failure", retryable=True),
        )
    record = await store.get(seed.identity.run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    return runtime, execution, store
