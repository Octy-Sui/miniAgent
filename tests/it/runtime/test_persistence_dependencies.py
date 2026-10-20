from typing import Any

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.runtime import AgentRuntime
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import QueueProvider, agent, final_response


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
