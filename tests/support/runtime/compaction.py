from dataclasses import replace

from miniagent.artifacts import FileArtifactStore
from miniagent.runtime.context import validate_summary
from miniagent.runtime.events import NewRunEvent
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.storage.sqlite import SQLiteRunStore
from tests.support.runtime.context import summary_plan, summary_response


async def journal_candidate(tmp_path):
    plan = summary_plan()
    store = SQLiteRunStore(tmp_path / "atomic.sqlite3")
    await store.initialize()
    recorder = RunRecorder(
        run_id=plan.state.identity.run_id, store=store, recovery_context=None
    )
    journal = RunJournal(recorder)
    await journal.create(plan.state)
    from miniagent.runtime.events import RunStarted

    await journal.start_run(
        plan.state,
        [NewRunEvent(run_id=plan.state.identity.run_id, payload=RunStarted())],
    )
    plan = replace(plan, source_snapshot_version=1, source_event_sequence=1)
    conversation = await validate_summary(summary_response(), plan, store)
    source = await store.load_version(plan.state.identity.run_id, 1)
    assert source is not None
    state, event = journal.prepare_compaction(
        plan.state,
        conversation,
        plan,
        attempts=(),
        new_tokens=1000,
        source_snapshot_id=source.snapshot_id,
    )
    return store, journal, plan, state, event


class PipelineEstimator:
    def __init__(self, old=10001, summary=3000, candidate=1000):
        self.old, self.summary, self.candidate = old, summary, candidate

    def estimate(self, request):
        if "summary_schema" in (request.messages[-1].content or ""):
            return self.summary
        if len(request.messages) > 2 and "Historical continuation summary" in (
            request.messages[2].content or ""
        ):
            return self.candidate
        return self.old if len(request.messages) >= 10 else 1000


async def pipeline(tmp_path, *, old=10001, summary=3000, candidate=1000, response=None):
    from miniagent.runtime import AgentRuntime
    from miniagent.runtime.execution import RunExecution
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import QueueProvider

    plan = summary_plan()
    store = SQLiteRunStore(tmp_path / "pipeline.sqlite3")
    await store.initialize()
    from miniagent.runtime.state import RunLifecycle

    seed = plan.state.model_copy(
        update={
            "lifecycle": RunLifecycle(updated_at=plan.state.lifecycle.updated_at),
            "execution": plan.state.execution.model_copy(update={"cursor": None}),
        }
    )
    execution = await RunExecution.start(
        seed,
        RunJournal(
            RunRecorder(
                run_id=plan.state.identity.run_id, store=store, recovery_context=None
            )
        ),
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    )
    provider = QueueProvider([response or summary_response()])
    runtime = AgentRuntime(
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
        estimator=PipelineEstimator(old, summary, candidate),
    )
    return runtime, execution, provider, store
