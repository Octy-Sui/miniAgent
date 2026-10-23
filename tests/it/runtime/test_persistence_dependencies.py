from typing import Any

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.runtime import AgentRuntime
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.store_contracts import RunNotFoundError
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import (
    QueueProvider,
    agent,
    final_response,
    run_state,
)


@pytest.mark.parametrize(("missing",), [(True,), (False,)])
def test_recorder_rejects_missing_store_before_creation(missing):
    kwargs: dict[str, Any] = {} if missing else {"store": None}
    with pytest.raises((TypeError, ValueError), match="store"):
        RunRecorder(
            run_id=run_state().identity.run_id,
            recovery_context=None,
            **kwargs,
        )


@pytest.mark.parametrize(("missing",), [(True,), (False,)])
async def test_execution_rejects_missing_artifacts_before_creation(tmp_path, missing):
    store = SQLiteRunStore(tmp_path / "uncreated.db")
    await store.initialize()
    state = run_state()
    journal = RunJournal(
        RunRecorder(run_id=state.identity.run_id, store=store, recovery_context=None)
    )
    kwargs: dict[str, Any] = {} if missing else {"artifact_store": None}
    with pytest.raises((TypeError, ValueError), match="artifact_store"):
        await RunExecution.start(state, journal, **kwargs)
    with pytest.raises(RunNotFoundError, match=str(state.identity.run_id)):
        await store.get(state.identity.run_id)


@pytest.mark.parametrize(
    ("dependency", "missing"),
    [
        ("run_store", True),
        ("artifact_store", True),
        ("run_store", False),
        ("artifact_store", False),
    ],
)
def test_dependencies_fail_before_provider(tmp_path, dependency, missing):
    provider = QueueProvider([])
    kwargs: dict[str, Any] = {
        "provider": provider,
        "tools": ToolRegistry([]),
        "run_store": SQLiteRunStore(tmp_path / "run.db"),
        "artifact_store": FileArtifactStore(tmp_path / "artifacts"),
    }
    if missing:
        del kwargs[dependency]
    else:
        kwargs[dependency] = None
    with pytest.raises((TypeError, ValueError), match=dependency):
        AgentRuntime(**kwargs)
    assert provider.requests == []


async def test_evidence_survives_reopen(tmp_path):
    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    result = await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=FileArtifactStore(tmp_path / "artifacts"),
    ).run(agent(), "task")
    reopened = SQLiteRunStore(tmp_path / "run.db")
    events = await reopened.list_events(result.run_id)
    assert all(event.schema_version == 3 for event in events)
    snapshot = await reopened.load_latest(result.run_id)
    assert snapshot is not None and snapshot.schema_version == 3
    assert any(event.type.value == "model_responded" for event in events)
