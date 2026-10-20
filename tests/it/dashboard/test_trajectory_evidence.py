from miniagent.dashboard.redaction import redact_json


def test_nested_model_evidence_keeps_usage_counts_and_redacts_body_credentials():
    projected = redact_json(
        {
            "type": "model_responded",
            "response": {
                "usage": {"input_tokens": 10, "reasoning_output_tokens": 2},
                "message": {"content": "api_key=forbidden"},
            },
        },
        schema="event",
    )
    assert isinstance(projected, dict)
    response = projected["response"]
    assert isinstance(response, dict)
    usage = response["usage"]
    assert isinstance(usage, dict)
    assert usage["input_tokens"] == 10
    assert usage["reasoning_output_tokens"] == 2
    assert "forbidden" not in str(projected)


async def test_new_model_evidence_uses_existing_bounded_redacted_view(
    tmp_path, runtime_persistence
):
    from datetime import UTC, datetime
    from time import monotonic

    from miniagent.dashboard.cursors import PageBoundary
    from miniagent.dashboard.parameters import EventFilters
    from miniagent.dashboard.projections.events import project_events
    from miniagent.dashboard.queries.events import read_events
    from miniagent.dashboard.reads import ReadBudget
    from miniagent.runtime import AgentRuntime
    from miniagent.tools.registry import ToolRegistry
    from tests.support.runtime.runtime import QueueProvider, agent, final_response

    store = runtime_persistence.store
    result = await AgentRuntime(
        provider=QueueProvider([final_response("api_key=not-visible")]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=runtime_persistence.artifacts,
    ).run(agent(), "task")
    record = await store.get(result.run_id)
    boundary = PageBoundary(
        run_id=result.run_id,
        as_of_sequence=record.last_event_sequence,
        latest_snapshot_version=record.latest_snapshot_version,
        run_status=record.state.lifecycle.status,
        read_at=datetime.now(UTC),
    )
    batch = read_events(
        store.path,
        boundary,
        EventFilters(),
        budget=ReadBudget(deadline=monotonic() + 5),
    )
    projected = project_events(batch)
    assert any(event.type == "model_responded" for event in projected)
    assert "not-visible" not in str(projected)
    assert sum(item.event is not None for item in batch.items) <= 16
