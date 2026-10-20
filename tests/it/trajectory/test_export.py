import json
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactWrite, FileArtifactStore
from miniagent.runtime import AgentRuntime
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry
from miniagent.trajectory import (
    TrajectoryDefinition,
    TrajectoryExportError,
    encode_trajectory,
)
from tests.support.runtime.runtime import QueueProvider, agent, final_response


async def exported(tmp_path, *, corrupt=False, config=None):
    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    result = await AgentRuntime(
        provider=QueueProvider([final_response("final 汉字")]),
        config=config,
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    ).run(agent(), 'task "quoted"')
    record = await store.get(result.run_id)
    await artifacts.put(ArtifactWrite(run_id=result.run_id, type="report"), b"report")
    await artifacts.put(
        ArtifactWrite(run_id=result.run_id, type="run_trajectory"), b"old"
    )
    await artifacts.put(ArtifactWrite(run_id=uuid4(), type="report"), b"other Run")
    if corrupt:
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER run_events_no_delete")
            connection.execute("DELETE FROM run_events WHERE sequence=2")
    definition = TrajectoryDefinition(
        task=record.state.identity.task,
        agent=record.state.definition.agent_spec,
        tools=record.state.definition.tool_definitions,
        budgets=record.state.definition.config,
    )
    content = await encode_trajectory(
        record=record,
        definition=definition,
        store=store,
        artifacts=artifacts,
        exported_at=datetime.now(UTC),
        producer_version="test",
        deadline=monotonic() + 10,
    )
    return json.loads(content), record, store


async def test_export_faithful_fixed_boundary(tmp_path):
    document, record, store = await exported(tmp_path)
    assert document["kind"] == "miniagent.trajectory"
    assert document["schema_version"] == 1
    assert document["run_id"] == str(record.state.identity.run_id)
    assert document["trace_id"] == record.state.identity.run_id.hex
    assert document["boundary"]["through_sequence"] == record.last_event_sequence
    assert [e["sequence"] for e in document["events"]] == list(
        range(1, record.last_event_sequence + 1)
    )
    assert document["definition"]["task"] == 'task "quoted"'
    assert document["definition"][
        "agent"
    ] == record.state.definition.agent_spec.model_dump(mode="json")
    events = await store.list_events(record.state.identity.run_id)
    for source, projected in zip(events, document["events"], strict=True):
        if source.type.value == "model_requested":
            assert projected["payload"]["request"] == source.payload.request.model_dump(
                mode="json"
            )
    assert document["summary"]["final_output"] == "final 汉字"
    assert isinstance(document["summary"]["usage"]["cost_usd"], str)
    assert len(document["artifacts"]) == 1
    assert document["artifacts"][0]["relation"] == "registered_only"
    assert document["coverage"]["status"] == "complete"


async def test_gap_is_structural_failure(tmp_path):
    with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
        await exported(tmp_path, corrupt=True)


async def encode_existing(record, store, artifacts, **changes):
    kwargs = {
        "record": record,
        "definition": TrajectoryDefinition(
            task=record.state.identity.task,
            agent=record.state.definition.agent_spec,
            tools=record.state.definition.tool_definitions,
            budgets=record.state.definition.config,
        ),
        "store": store,
        "artifacts": artifacts,
        "exported_at": datetime(2026, 10, 20, tzinfo=UTC),
        "producer_version": "test",
        "deadline": monotonic() + 30,
    }
    kwargs.update(changes)
    return await encode_trajectory(**kwargs)


async def test_exact_total_json_limit_including_header_and_escaping(tmp_path):
    from miniagent.trajectory.export import MAX_JSON_BYTES

    _, record, store = await exported(tmp_path)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    baseline = await encode_existing(record, store, artifacts, producer_version='"汉\n')
    padding = MAX_JSON_BYTES - len(baseline)
    version = '"汉\n' + "x" * padding
    content = await encode_existing(record, store, artifacts, producer_version=version)
    assert len(content) == 67_108_864
    one_less = await encode_existing(
        record, store, artifacts, producer_version=version[:-1]
    )
    assert len(one_less) == 67_108_863
    for payload in (one_less, content):
        ref = await artifacts.put(
            ArtifactWrite(run_id=record.state.identity.run_id, type="run_trajectory"),
            payload,
        )
        manifest = await artifacts.get_record(ref)
        assert manifest.size_bytes == len(payload)
        assert manifest.sha256 == sha256(payload).hexdigest()
        assert await artifacts.read(ref) == payload
    del one_less
    assert json.loads(content)["producer_version"] == version
    del content
    with pytest.raises(TrajectoryExportError, match="TOO_LARGE"):
        await encode_existing(record, store, artifacts, producer_version=version + "x")


async def test_deadline_is_not_reset(tmp_path):
    _, record, store = await exported(tmp_path)
    with pytest.raises(TrajectoryExportError, match="TIMEOUT"):
        await encode_existing(
            record,
            store,
            FileArtifactStore(tmp_path / "artifacts"),
            deadline=monotonic() - 1,
        )


async def test_definition_mismatch_is_rejected(tmp_path):
    _, record, store = await exported(tmp_path)
    definition = TrajectoryDefinition(
        task="wrong",
        agent=record.state.definition.agent_spec,
        budgets=record.state.definition.config,
    )
    with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
        await encode_existing(
            record,
            store,
            FileArtifactStore(tmp_path / "artifacts"),
            definition=definition,
        )


async def test_older_boundary_excludes_new_events_and_uses_pages(tmp_path):
    from miniagent.runtime.events import NewRunEvent, ProviderIdentityChanged

    _, record, store = await exported(tmp_path)
    sequence = record.last_event_sequence
    for _ in range(40):
        await store.append(
            NewRunEvent(
                run_id=record.state.identity.run_id,
                payload=ProviderIdentityChanged(
                    original_endpoint_identity="https://old.test",
                    current_endpoint_identity="https://new.test",
                ),
            ),
            expected_sequence=sequence,
        )
        sequence += 1

    class ReadBoundary:
        def __getattr__(self, name):
            return getattr(store, name)

        def __init__(self):
            self.pages = []

        async def list_events(self, run_id, **kwargs):
            assert kwargs["limit"] <= 16
            assert kwargs["through_sequence"] == record.last_event_sequence
            self.pages.append(kwargs)
            return await store.list_events(run_id, **kwargs)

    boundary = ReadBoundary()
    document = json.loads(
        await encode_existing(
            record, boundary, FileArtifactStore(tmp_path / "artifacts")
        )
    )
    assert len(document["events"]) == record.last_event_sequence
    assert document["summary"]["final_output"] == record.state.lifecycle.final_output
    assert boundary.pages


async def test_wrong_attempt_is_structural_failure(tmp_path):
    from uuid import uuid4

    _, record, store = await exported(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET payload_json=json_set(payload_json,'$.attempt_id',?) WHERE type='model_responded'",
            (str(uuid4()),),
        )
    with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
        await encode_existing(record, store, FileArtifactStore(tmp_path / "artifacts"))


async def test_registered_metadata_does_not_verify_body(tmp_path):
    _, record, store = await exported(tmp_path)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    deleted = 0
    for body in (tmp_path / "artifacts" / "content").rglob("*"):
        if body.is_file():
            body.unlink()
            deleted += 1
    assert deleted > 0
    document = json.loads(await encode_existing(record, store, artifacts))
    assert document["artifacts"][0]["body_verified"] is False
    assert document["artifacts"][0]["relation"] == "registered_only"


async def test_unavailable_observation_is_explicit_partial(tmp_path):
    from miniagent.runtime import RuntimeConfig
    from miniagent.tools.contracts import ToolResult
    from tests.support.runtime.runtime import (
        AllowPolicyEngine,
        SuccessfulTool,
        tool_response,
    )

    class UnrenderableTool(SuccessfulTool):
        async def execute(self, arguments, *, tool_call_id):
            return ToolResult(
                tool_call_id=tool_call_id,
                status="success",
                duration_ms=1 << 100,
                content="raw retained",
            )

    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    result = await AgentRuntime(
        provider=QueueProvider([tool_response()]),
        tools=ToolRegistry([UnrenderableTool()]),
        run_store=store,
        artifact_store=artifacts,
        policy_engine=AllowPolicyEngine(),
        config=RuntimeConfig(max_steps=1),
    ).run(agent(allowed_tools=["ok"]), "task")
    record = await store.get(result.run_id)
    document = json.loads(await encode_existing(record, store, artifacts))
    assert document["coverage"]["status"] == "partial"
    issue = next(
        i for i in document["coverage"]["issues"] if i["code"] == "OBSERVATION_MISSING"
    )
    assert issue["sequence"] > 0 and issue["attempt_id"]
    ended = next(e for e in document["events"] if e["payload"]["type"] == "tool_end")[
        "payload"
    ]
    assert ended["raw_result"]["result"]["content"] == "raw retained"
    assert ended["observation"] is None


async def test_frozen_definition_positive_whitelist_and_credential_exclusion(tmp_path):
    from decimal import Decimal

    from pydantic import ValidationError

    from miniagent.metering.pricing import ModelPrice, PriceConfig

    _, record, store = await exported(tmp_path)
    base = {
        "task": record.state.identity.task,
        "agent": record.state.definition.agent_spec,
        "tools": record.state.definition.tool_definitions,
        "budgets": record.state.definition.config,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TrajectoryDefinition.model_validate(
            {**base, "settings": {"api_key": "secret-negative"}}
        )
    definition = TrajectoryDefinition(
        **base,
        pricing=PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="model",
                    input_per_token=Decimal("0.000001"),
                    output_per_token=Decimal("0.000002"),
                )
            ]
        ),
        model_limits={"context_window_tokens": 12345, "max_output_tokens": 2048},
        workspace_kind="local",
        workspace_id="workspace-positive",
        base_revision="base-positive",
        evaluator="pass_through",
        benchmark={"instance_id": "benchmark-positive", "task_digest": "a" * 64},
        provider_endpoint_identity="https://endpoint.test/v1",
    )
    content = await encode_existing(
        record, store, FileArtifactStore(tmp_path / "artifacts"), definition=definition
    )
    exported_definition = json.loads(content)["definition"]
    assert exported_definition == definition.model_dump(mode="json")
    assert "secret-negative" not in content.decode()
    assert "recovery_context" not in json.loads(content)


@pytest.mark.parametrize(
    "variant", ["invalid_json", "unknown_type", "summary", "wrong_run"]
)
async def test_structural_sources_are_rejected(tmp_path, variant):
    from uuid import uuid4

    _, record, store = await exported(tmp_path)
    if variant == "summary":
        record = record.model_copy(
            update={
                "state": record.state.model_copy(
                    update={
                        "lifecycle": record.state.lifecycle.model_copy(
                            update={"final_output": "fabricated"}
                        )
                    }
                )
            }
        )
    elif variant == "wrong_run":
        backing = store
        original = store.list_events

        async def wrong_run(*args, **kwargs):
            page = await original(*args, **kwargs)
            page[0] = page[0].model_copy(update={"run_id": uuid4()})
            return page

        class WrongRunStore:
            def __getattr__(self, name):
                return wrong_run if name == "list_events" else getattr(backing, name)

        store = WrongRunStore()
    else:
        with sqlite3.connect(store.path) as connection:
            connection.execute("DROP TRIGGER run_events_no_update")
            connection.execute("PRAGMA ignore_check_constraints=ON")
            if variant == "invalid_json":
                connection.execute(
                    "UPDATE run_events SET payload_json='broken' WHERE sequence=2"
                )
            else:
                connection.execute(
                    "UPDATE run_events SET payload_json=json_set(payload_json,'$.type','future_unknown') WHERE sequence=2"
                )
    with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
        await encode_existing(record, store, FileArtifactStore(tmp_path / "artifacts"))


@pytest.mark.parametrize("verdict", ["pass_through", "resolved", "unresolved", "error"])
async def test_exact_cost_tokens_and_evaluation_category(tmp_path, verdict):
    from decimal import Decimal

    from miniagent.evaluation import EvaluationError, EvaluationResult
    from miniagent.metering.pricing import ModelPrice, PriceConfig
    from miniagent.models.messages import AssistantMessage
    from miniagent.models.protocol import ModelResponse, ModelUsage

    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    pricing = PriceConfig(
        models=[
            ModelPrice(
                provider="test",
                model="test-model",
                input_per_token=Decimal("0.000001"),
                output_per_token=Decimal("0.000002"),
            )
        ]
    )
    expected = EvaluationResult(
        passed=verdict in ("pass_through", "resolved"),
        output=verdict,
        error=EvaluationError(code="HARNESS_ERROR", message="external error")
        if verdict == "error"
        else None,
    )

    class Evaluator:
        calls = 0

        async def evaluate(self, request):
            self.calls += 1
            return expected

    evaluator = Evaluator()
    response = ModelResponse(
        message=AssistantMessage(content="普通解释 api_key=allowed-body"),
        finish_reason="stop",
        usage=ModelUsage(
            input_tokens=10, output_tokens=5, total_tokens=15, reasoning_output_tokens=2
        ),
    )
    result = await AgentRuntime(
        provider=QueueProvider([response]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
        pricing=pricing,
        evaluator=evaluator,
    ).run(agent(), "task")
    record = await store.get(result.run_id)
    document = json.loads(await encode_existing(record, store, artifacts))
    responded = next(
        e["payload"]
        for e in document["events"]
        if e["payload"]["type"] == "model_responded"
    )
    assert Decimal(responded["request_cost"]["total_cost"]) == Decimal("0.000020")
    assert Decimal(document["summary"]["usage"]["cost_usd"]) == Decimal("0.000020")
    assert responded["response"]["usage"]["reasoning_output_tokens"] == 2
    assert (
        responded["response"]["message"]["content"] == "普通解释 api_key=allowed-body"
    )
    evaluation = next(
        e["payload"]["result"]
        for e in document["events"]
        if e["payload"]["type"] == "evaluation_completed"
    )
    assert evaluation == expected.model_dump(mode="json")
    assert evaluator.calls == 1
    assert document["coverage"]["content_policy"] == "faithful"


async def test_suspended_unknown_request_is_complete_without_fabricated_response(
    tmp_path,
):
    import asyncio

    from miniagent.runtime.interruption import InterruptionToken
    from tests.support.runtime.runtime import BlockingProvider

    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    provider = BlockingProvider()
    token = InterruptionToken()
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
    )
    running = asyncio.create_task(
        runtime.run(agent(), "task", interruption_token=token)
    )
    await asyncio.wait_for(provider.started.wait(), 2)
    token.request(2)
    result = await running
    record = await store.get(result.run_id)
    document = json.loads(await encode_existing(record, store, artifacts))
    assert document["boundary"]["kind"] == "suspended"
    assert document["boundary"]["terminal"] is False
    assert document["coverage"]["status"] == "complete"
    assert len(document["coverage"]["unknown_requests"]) == 1
    assert (
        document["summary"]["unconfirmed"]
        == record.state.usage.unconfirmed_model_requests
    )
    assert not any(
        e["payload"]["type"] == "model_responded" for e in document["events"]
    )


async def test_snapshot_after_boundary_is_rejected(tmp_path):
    _, record, store = await exported(tmp_path)
    snapshot = await store.load_version(
        record.state.identity.run_id, record.latest_snapshot_version
    )
    future = snapshot.model_copy(
        update={"event_sequence": record.last_event_sequence + 1}
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER run_snapshots_no_update")
        connection.execute(
            "UPDATE run_snapshots SET event_sequence=?,snapshot_json=? WHERE version=?",
            (future.event_sequence, future.model_dump_json(), future.version),
        )
    with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
        await encode_existing(record, store, FileArtifactStore(tmp_path / "artifacts"))


async def test_sequence_order_survives_nonmonotonic_timestamps(tmp_path):
    _, record, store = await exported(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        connection.execute(
            "UPDATE run_events SET occurred_at='2099-01-01T00:00:00Z' WHERE sequence=1"
        )
    document = json.loads(
        await encode_existing(record, store, FileArtifactStore(tmp_path / "artifacts"))
    )
    assert document["events"][0]["occurred_at"] > document["events"][1]["occurred_at"]
    assert [event["sequence"] for event in document["events"]] == list(
        range(1, record.last_event_sequence + 1)
    )


async def test_multi_page_metadata_escaping_counts_against_real_limit(tmp_path):
    _, record, store = await exported(tmp_path)
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    text = "汉" * 60_000
    for _ in range(201):
        await artifacts.put(
            ArtifactWrite(
                run_id=record.state.identity.run_id,
                type="report",
                metadata={"text": text},
            ),
            b"",
        )
    assert len(text.encode()) * 201 < 67_108_864
    with pytest.raises(TrajectoryExportError, match="TOO_LARGE"):
        await encode_existing(record, store, artifacts)


async def test_catalog_includes_typed_evaluation_check_references(tmp_path):
    from uuid import uuid4

    from miniagent.evaluation import EvaluationCheck, EvaluationResult

    run_id = uuid4()
    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    report = await artifacts.put(
        ArtifactWrite(run_id=run_id, type="evaluation_report"), b"report"
    )

    class Evaluator:
        async def evaluate(self, request):
            return EvaluationResult(
                passed=True,
                checks=[EvaluationCheck(name="check", passed=True, artifact=report)],
            )

    await AgentRuntime(
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
        artifact_store=artifacts,
        evaluator=Evaluator(),
    ).run(agent(), "task", run_id=run_id)
    record = await store.get(run_id)
    document = json.loads(await encode_existing(record, store, artifacts))
    item = next(
        item
        for item in document["artifacts"]
        if item["artifact_id"] == str(report.artifact_id)
    )
    assert item["relation"] == "event_referenced"


@pytest.mark.parametrize(
    "variant",
    [
        "execution_attempt",
        "tool_arguments",
        "ended_identity",
        "missing_response",
        "missing_output",
        "partial_stream",
    ],
)
async def test_execution_references_and_explicit_partial_evidence(
    tmp_path, submission_case, variant
):
    from uuid import uuid4

    from miniagent.runtime import RuntimeConfig
    from miniagent.tools.shell import ShellTool
    from tests.support.runtime.runtime import AllowPolicyEngine, tool_response

    workspace, artifacts = submission_case
    store = SQLiteRunStore(tmp_path / "run.db")
    await store.initialize()
    result = await AgentRuntime(
        provider=QueueProvider(
            [tool_response(name="shell", arguments={"command": "printf done"})]
        ),
        tools=ToolRegistry([ShellTool(workspace)]),
        config=RuntimeConfig(max_steps=1),
        run_store=store,
        artifact_store=artifacts,
        policy_engine=AllowPolicyEngine(),
    ).run(agent(allowed_tools=["shell"]), "task", run_id=workspace.run_id)
    record = await store.get(result.run_id)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        if variant == "execution_attempt":
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.attempt_id',?) WHERE type='tool_execution_end'",
                (str(uuid4()),),
            )
        elif variant == "tool_arguments":
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.arguments.command','printf forged') WHERE type='tool_start'"
            )
        elif variant == "ended_identity":
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.evidence.identity.pid',1) WHERE type='operation_execution_ended'"
            )
        elif variant == "missing_response":
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.response',NULL) WHERE type='model_responded'"
            )
        elif variant == "missing_output":
            connection.execute(
                "UPDATE run_events SET type='provider_identity_changed',payload_json=? WHERE type='operation_output_recorded'",
                (
                    json.dumps(
                        {
                            "type": "provider_identity_changed",
                            "original_endpoint_identity": "https://old.test",
                            "current_endpoint_identity": "https://new.test",
                        }
                    ),
                ),
            )
        else:
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.output.stdout.status','partial','$.output.stdout.eof_observed',json('false'),'$.output.stdout.reason','drain interrupted') WHERE type='operation_output_recorded'"
            )
    if variant in ("execution_attempt", "tool_arguments", "ended_identity"):
        with pytest.raises(TrajectoryExportError, match="STRUCTURE"):
            await encode_existing(record, store, artifacts)
    else:
        document = json.loads(await encode_existing(record, store, artifacts))
        expected = {
            "missing_response": "RESPONSE_MISSING",
            "missing_output": "OUTPUT_MISSING",
            "partial_stream": "STREAM_PARTIAL",
        }[variant]
        assert document["coverage"]["status"] == "partial"
        issue = next(
            item for item in document["coverage"]["issues"] if item["code"] == expected
        )
        assert issue["sequence"] > 0 and issue["attempt_id"] and issue["reason"]
