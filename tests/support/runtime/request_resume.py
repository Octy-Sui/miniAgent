from uuid import uuid4

from miniagent.artifacts import FileArtifactStore
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.runtime import (
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from tests.support.runtime.runtime import agent
from tests.support.trajectory.fixtures import request_for


async def started(tmp_path, *, config=None):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    spec = agent()
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=spec,
        config=config or RuntimeConfig(),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=state.identity.run_id, store=store, recovery_context=None
            )
        ),
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    )
    await execution.begin_step(
        provider="test", model="test-model", request=request_for(execution.state)
    )
    return store, execution


async def reopen(store, execution):
    record = await store.get(execution.state.identity.run_id)
    return RunExecution.attach(
        record,
        RunJournal.attach(RunRecorder.attach(store, record), []),
        artifact_store=execution.artifact_store,
    )
