import json
import sqlite3
from pathlib import Path

import pytest

from miniagent.artifacts import ArtifactPersistenceError, FileArtifactStore
from miniagent.runtime import AgentRuntime, RuntimeConfig
from miniagent.runtime.events import ToolEnded
from miniagent.runtime.recording import RunPersistenceError
from miniagent.storage import SQLiteRunStore
from miniagent.tools.base import ToolArguments
from miniagent.tools.contracts import ToolError, ToolResult, ToolStatus
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    SuccessfulTool,
    agent,
    tool_response,
)


class Arguments(ToolArguments):
    value: str = "default"


class ReturnedTool(SuccessfulTool):
    arguments_model = Arguments

    def __init__(self, returned, log: Path):
        super().__init__()
        self.returned = returned
        self.log = log

    async def execute(self, arguments, *, tool_call_id):
        with self.log.open("a") as stream:
            stream.write("invoked\n")
        if isinstance(self.returned, Exception):
            raise self.returned
        return self.returned


def sized_result(
    size: int, *, wrong_id: bool = False, wrong_code: bool = False
) -> ToolResult:
    result = ToolResult(
        tool_call_id="wrong-id" if wrong_id else "call",
        status=ToolStatus.ERROR if wrong_code else ToolStatus.SUCCESS,
        content="",
        error=ToolError(code="bad code", message="original error")
        if wrong_code
        else None,
    )
    padding = size - len(result.model_dump_json().encode())
    assert padding > 0
    return result.model_copy(update={"content": "x" * padding})


async def run_return(tmp_path, returned, *, allowed=True, invalid_arguments=False):
    store = SQLiteRunStore(tmp_path / "runs.db")
    await store.initialize()
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    provider = QueueProvider(
        [tool_response(arguments={"invalid": True} if invalid_arguments else {})]
    )
    runtime = AgentRuntime(
        provider=provider,
        tools=ToolRegistry([ReturnedTool(returned, tmp_path / "calls")]),
        run_store=store,
        artifact_store=artifacts,
        config=RuntimeConfig(max_steps=1),
        policy_engine=AllowPolicyEngine(),
    )
    result = await runtime.run(agent(allowed_tools=["ok"] if allowed else []), "task")
    record = await store.get(result.run_id)
    ended = next(
        event
        for event in await store.list_events(result.run_id)
        if isinstance(event.payload, ToolEnded)
    )
    assert isinstance(ended.payload, ToolEnded)
    return result, record, ended.payload, artifacts, provider


@pytest.mark.parametrize(
    "size", [65535, 65536, 65537], ids=["TC-U07-05", "TC-U07-06", "TC-U07-07"]
)
async def test_raw_size_boundaries_and_exact_final_observation(tmp_path, size):
    original = sized_result(size)
    assert len(original.model_dump_json().encode()) == size
    _result, record, event, artifacts, provider = await run_return(tmp_path, original)
    ended = event
    assert ended.return_validation.disposition == "accepted"
    if size <= 65536:
        assert ended.raw_result.kind == "inline"
        assert ended.raw_result.result == original
    else:
        assert ended.raw_result.kind == "artifact"
        assert (
            ToolResult.model_validate_json(
                await FileArtifactStore(artifacts.root).read(ended.raw_result.ref)
            )
            == original
        )
    assert len(ended.result.content.encode()) <= 16384
    assert ended.result.output.content_truncated
    assert ended.observation == record.state.conversation.messages[-1]
    assert ToolResult.model_validate_json(ended.observation.content) == ended.result
    assert len(provider.requests) == 1
    queried = await SQLiteRunStore(tmp_path / "runs.db").list_tool_results(
        record.state.identity.run_id, step_number=1
    )
    assert queried[0].result == ended.result
    snapshot = await SQLiteRunStore(tmp_path / "runs.db").load_latest(
        record.state.identity.run_id
    )
    assert snapshot is not None
    assert original.content not in snapshot.model_dump_json()
    assert len(snapshot.model_dump_json().encode()) < size


@pytest.mark.parametrize("variant", ["wrong_id", "wrong_code"])
@pytest.mark.parametrize("size", [1024, 70000])
async def test_tc_u07_02(tmp_path, variant, size):
    original = sized_result(
        size, wrong_id=variant == "wrong_id", wrong_code=variant == "wrong_code"
    )
    result, _record, event, artifacts, provider = await run_return(tmp_path, original)
    ended = event
    assert ended.tool_call_id == "call"
    assert ended.result.error.code == "INVALID_TOOL_RESULT"
    assert ended.return_validation.disposition == "rejected"
    assert ended.return_validation.reason == (
        "tool_call_id_mismatch" if variant == "wrong_id" else "invalid_error_code"
    )
    if ended.raw_result.kind == "inline":
        assert ended.raw_result.result == original
    else:
        assert (
            ToolResult.model_validate_json(await artifacts.read(ended.raw_result.ref))
            == original
        )
        manifest = await artifacts.get_record(ended.raw_result.ref)
        assert manifest.run_id == result.run_id
        assert manifest.metadata["tool_call_id"] == "call"
        assert manifest.metadata["attempt_id"] == str(ended.attempt_id)
    assert len(provider.requests) == 1


async def test_tc_u07_08(tmp_path):
    original = ToolResult(
        tool_call_id="call", status="success", content='汉"\\\n' * 8000
    )
    assert len(original.content) < 65536 < len(original.model_dump_json().encode())
    _result, _record, event, artifacts, _provider = await run_return(tmp_path, original)
    assert event.raw_result.kind == "artifact"
    assert (
        ToolResult.model_validate_json(await artifacts.read(event.raw_result.ref))
        == original
    )


async def test_tc_u07_03_no_arbitrary_representation(tmp_path):
    log = tmp_path / "representation"

    class Unsupported:
        def __repr__(self):
            log.write_text("repr")
            return "unsupported"

        def __reduce__(self):
            log.write_text("reduce")
            raise RuntimeError("must not pickle")

    _result, _record, event, _artifacts, _provider = await run_return(
        tmp_path, Unsupported()
    )
    assert event.raw_result is None
    assert event.return_validation.reason == "unsupported_result_type"
    assert event.result.error.code == "INVALID_TOOL_RESULT"
    assert not log.exists()


@pytest.mark.parametrize(
    "variant",
    ["success", "error", "timeout", "denied", "invalid_arguments", "exception"],
)
async def test_tc_u10_01_and_u07_04(tmp_path, variant):
    returned = (
        RuntimeError("tool exception")
        if variant == "exception"
        else ToolResult(
            tool_call_id="call",
            status=variant if variant in {"error", "timeout"} else "success",
            content="original outcome",
            error=ToolError(code="TOOL_FAILED", message="failure")
            if variant in {"error", "timeout"}
            else None,
        )
    )
    _result, record, event, _artifacts, provider = await run_return(
        tmp_path,
        returned,
        allowed=variant != "denied",
        invalid_arguments=variant == "invalid_arguments",
    )
    ended = event
    assert ended.observation == record.state.conversation.messages[-1]
    assert ToolResult.model_validate_json(ended.observation.content) == ended.result
    if variant in {"denied", "invalid_arguments", "exception"}:
        assert ended.raw_result is None
        assert ended.return_validation.disposition == "not_returned"
    assert len(provider.requests) == 1


async def test_tc_u10_03_unrenderable_metadata_keeps_raw_without_observation(tmp_path):
    original = ToolResult(
        tool_call_id="call",
        status="success",
        content="original outcome",
        duration_ms=1 << 100,
    )
    result, record, event, _artifacts, provider = await run_return(tmp_path, original)
    assert event.raw_result.result == original
    assert event.observation is None
    assert event.observation_missing_reason is not None
    assert all(message.role != "tool" for message in record.state.conversation.messages)
    assert result.error.code == "TOOL_OBSERVATION_UNAVAILABLE"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("fault", ["manifest", "event"])
async def test_tc_u11_02_and_04(tmp_path, monkeypatch, fault):
    import os

    original = sized_result(70000, wrong_id=True)
    if fault == "manifest":
        replace = os.replace

        def fail_manifest(source, target):
            if Path(target).suffix == ".json":
                raise OSError("manifest failed")
            return replace(source, target)

        monkeypatch.setattr(os, "replace", fail_manifest)
    else:
        store = SQLiteRunStore(tmp_path / "runs.db")
        await store.initialize()
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_tool_end AFTER INSERT ON run_events WHEN NEW.type='tool_end' BEGIN SELECT RAISE(ABORT,'tool result commit failed'); END"
            )
    with pytest.raises(
        ArtifactPersistenceError if fault == "manifest" else RunPersistenceError
    ):
        await run_return(tmp_path, original)
    assert (tmp_path / "calls").read_text() == "invoked\n"
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE type='tool_end'"
            ).fetchone()[0]
            == 0
        )
        state = json.loads(
            connection.execute("SELECT state_json FROM runs").fetchone()[0]
        )
    assert not any(
        message["role"] == "tool" for message in state["conversation"]["messages"]
    )


async def test_requested_and_canonical_arguments_are_separate(tmp_path):
    from miniagent.runtime.events import ToolExecutionStarted, ToolStarted

    _result, record, _ended, _artifacts, _provider = await run_return(
        tmp_path, ToolResult(tool_call_id="call", status="success", content="done")
    )
    events = await SQLiteRunStore(tmp_path / "runs.db").list_events(
        record.state.identity.run_id
    )
    started = next(
        event.payload for event in events if isinstance(event.payload, ToolStarted)
    )
    executed = next(
        event.payload
        for event in events
        if isinstance(event.payload, ToolExecutionStarted)
    )
    assert started.arguments == {}
    assert executed.canonical_arguments == {"value": "default"}


@pytest.mark.parametrize("variant", ["missing_raw", "observation_different"])
def test_tool_evidence_rejects_inconsistent_fields(variant):
    from pydantic import ValidationError

    from miniagent.models.messages import ToolMessage
    from miniagent.tools.contracts import InlineToolResultEvidence, ToolReturnValidation

    result = ToolResult(tool_call_id="call", status="success", content="visible")
    observed = (
        result.model_copy(update={"content": "different"})
        if variant == "observation_different"
        else result
    )
    with pytest.raises(ValidationError, match="raw|observation"):
        ToolEnded(
            call_index=0,
            tool_call_id="call",
            tool_name="ok",
            status="success",
            result=result,
            raw_result=None
            if variant == "missing_raw"
            else InlineToolResultEvidence(result=result),
            return_validation=ToolReturnValidation(disposition="accepted"),
            observation=ToolMessage(
                tool_call_id="call", content=observed.model_dump_json()
            ),
            observation_missing_reason=None,
        )


@pytest.mark.parametrize("variant", ["invalid_json", "payload_type"])
async def test_effective_projection_rejects_corrupt_source(tmp_path, variant):
    from miniagent.runtime.store_contracts import CorruptStoredDataError

    _result, record, _ended, _artifacts, _provider = await run_return(
        tmp_path, ToolResult(tool_call_id="call", status="success", content="visible")
    )
    with sqlite3.connect(tmp_path / "runs.db") as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        if variant == "invalid_json":
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                "UPDATE run_events SET payload_json='broken' WHERE type='tool_end'"
            )
        else:
            connection.execute(
                "UPDATE run_events SET payload_json=json_set(payload_json,'$.type','model_requested') WHERE type='tool_end'"
            )
    with pytest.raises(CorruptStoredDataError):
        await SQLiteRunStore(tmp_path / "runs.db").list_tool_results(
            record.state.identity.run_id, step_number=1
        )


async def test_raw_publication_obeys_late_cleanup_deadline(tmp_path, monkeypatch):
    import os
    from time import monotonic

    from miniagent.environments.managed_execution import cleanup_deadline_scope

    deadline = [None]
    original_write = os.write

    def expire_after_write(fd, data):
        written = original_write(fd, data)
        deadline[0] = monotonic() - 1
        return written

    monkeypatch.setattr(os, "write", expire_after_write)
    with (
        cleanup_deadline_scope(lambda: deadline[0]),
        pytest.raises(TimeoutError, match="deadline"),
    ):
        await run_return(tmp_path, sized_result(200000))
    assert not list((tmp_path / "artifacts").rglob("*.json"))


async def test_allowed_tool_body_preserves_sensitive_looking_text(tmp_path):
    sentinel = "api_key=synthetic-only; <thinking>ordinary tool text</thinking>"
    original = ToolResult(
        tool_call_id="call", status=ToolStatus.SUCCESS, content=sentinel
    )
    _, _, ended, _, _ = await run_return(tmp_path, original)
    assert ended.raw_result.kind == "inline"
    assert ended.raw_result.result.content == sentinel
    assert ToolResult.model_validate_json(ended.observation.content).content == sentinel
