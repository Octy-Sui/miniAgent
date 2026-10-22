import json
from datetime import UTC, datetime
from time import monotonic

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.models.protocol import ModelProviderError, ModelRequestTiming
from miniagent.runtime import AgentRuntime
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from miniagent.trajectory import TrajectoryDefinition, encode_trajectory
from tests.support.runtime.compaction import pipeline
from tests.support.runtime.runtime import QueueProvider, agent


@pytest.mark.parametrize("summary", [False, True])
async def test_failed_request_timing_survives_journal_and_trajectory(tmp_path, summary):
    timing = ModelRequestTiming(
        client_duration_ms=1700,
        server_duration_ms=1613,
        provider_request_id="request-id",
    )
    provider = QueueProvider([ModelProviderError("rejected", request_timing=timing)])
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    if summary:
        runtime, execution, _, store = await pipeline(tmp_path)
        runtime.provider = provider
        await runtime._prepare_request(execution, force=True)
        run_id = execution.state.identity.run_id
        kind = "compaction_attempt_recorded"
    else:
        store = SQLiteRunStore(tmp_path / "runs.sqlite3")
        await store.initialize()
        result = await AgentRuntime(
            provider=provider,
            tools=ToolRegistry([]),
            run_store=store,
            artifact_store=artifacts,
        ).run(agent(), "issue")
        run_id = result.run_id
        kind = "operation_interrupted"
    events = await store.list_events(run_id)
    event = next(e for e in events if e.type.value == kind)
    payload = event.payload.model_dump(mode="json")
    recorded = (
        payload["result"]["request_timing"] if summary else payload["request_timing"]
    )
    assert recorded["client_duration_ms"] == 1700
    assert recorded["server_duration_ms"] == 1613
    assert recorded["provider_request_id"] == "request-id"
    record = await store.get(run_id)
    document = json.loads(
        await encode_trajectory(
            record=record,
            definition=TrajectoryDefinition(
                task=record.state.identity.task,
                agent=record.state.definition.agent_spec,
                tools=record.state.definition.tool_definitions,
                budgets=record.state.definition.config,
            ),
            store=store,
            artifacts=artifacts,
            exported_at=datetime.now(UTC),
            producer_version="test",
            deadline=monotonic() + 10,
        )
    )
    exported = next(
        e["payload"] for e in document["events"] if e["payload"]["type"] == kind
    )
    assert (
        exported["result"]["request_timing"] if summary else exported["request_timing"]
    ) == recorded
