import asyncio
import gc
import hashlib
import json
import os
import resource
import shutil
import sqlite3
import sys
import time
import tracemalloc
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from miniagent.artifacts import FileArtifactStore
from miniagent.cli.resume import ResumeConfig, inspect_resume
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.projections.spans import project_spans
from miniagent.dashboard.queries.events import (
    locate_event,
    read_compaction_sources,
    read_events,
    read_scope_facts,
    read_tool_facts,
)
from miniagent.dashboard.reads import ReadBudget
from miniagent.environments import LocalWorkspaceBackend
from miniagent.environments.attachment import LocalAttachment
from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import execution_scope
from miniagent.models.protocol import ModelRequest
from miniagent.runtime import AgentRuntime
from miniagent.runtime.context import validate_compaction_record, validate_summary
from miniagent.runtime.events import RunEvent, RunEventType
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import ToolsDisposition
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.resume import ResumePlan
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolResult
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool
from tests.support.runtime.context import summary_plan, summary_response
from tests.support.runtime.runtime import tool_response
from tests.support.runtime.runtime_resume import completed_protocol
from tests.support.trajectory.fixtures import request_for


def retained_events(value: Any) -> int:
    seen = set()

    def visit(item):
        if id(item) in seen:
            return 0
        seen.add(id(item))
        if isinstance(item, RunEvent):
            return 1
        if isinstance(item, BaseModel):
            return sum(visit(getattr(item, key)) for key in type(item).model_fields)
        if is_dataclass(item) and not isinstance(item, type):
            return sum(visit(getattr(item, field.name)) for field in fields(item))
        if isinstance(item, dict):
            return sum(visit(child) for child in item.values())
        if isinstance(item, (tuple, list, set)):
            return sum(visit(child) for child in item)
        return 0

    return visit(value)


async def consume(root: Path, consumer: str) -> dict:
    os.environ.update(
        OPENAI_API_KEY="resource-fixture", OPENAI_BASE_URL="https://provider.test/v1"
    )
    metadata = json.loads((root / "history.json").read_text())
    run_id = UUID(metadata["run_id"])
    database = root / f"{consumer}.db"
    shutil.copy2(root / "runs.db", database)
    store = SQLiteRunStore(database)
    artifacts = FileArtifactStore(root / "artifacts")
    record = await store.get(run_id)
    queries = []
    live_event_max = [0]
    point_reads = [0]

    def trace(statement):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)
            if "SELECT * FROM run_events" in statement:
                if "event_id" in statement:
                    point_reads[0] += 1
                    if point_reads[0] != 1 and point_reads[0] % 16:
                        return
                live_event_max[0] = max(
                    live_event_max[0],
                    sum(isinstance(item, RunEvent) for item in gc.get_objects()),
                )

    connect = sqlite3.connect

    class TraceConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.set_trace_callback(trace)

    def traced(*args, **kwargs):
        return connect(*args, **kwargs, factory=TraceConnection)

    execution = RunExecution.attach(
        record,
        RunJournal.attach(RunRecorder.attach(store, record), []),
        artifact_store=artifacts,
    )
    workspace = None
    if consumer in ("g4", "g5", "g6"):
        context = record.recovery_context
        assert context is not None
        assert isinstance(context.attachment, LocalAttachment)
        assert isinstance(context.workspace_spec.options, LocalOptions)
        workspace = await LocalWorkspaceBackend(
            root=context.attachment.root, artifact_store=artifacts
        ).attach(
            LocalWorkspaceSpec(run_id=run_id, options=context.workspace_spec.options),
            context.attachment,
        )
    if consumer in ("g4", "g5") or consumer == "g8" and metadata["steps"] == 0:
        record = await completed_protocol(store, execution)
        execution = RunExecution.attach(
            record,
            RunJournal.attach(RunRecorder.attach(store, record), []),
            artifact_store=artifacts,
        )
    if consumer == "g8" and metadata["steps"] == 0:
        await execution.begin_model_retry(request=request_for(execution.state))
        await execution.record_model_response(
            tool_response(name="shell", arguments={"command": "printf done"}),
            None,
            disposition=ToolsDisposition(),
        )
        await execution.begin_tool(call_index=0, tool_call_id="call", tool_name="shell")
        from miniagent.tools.contracts import ToolError

        await execution.complete_tool(
            ToolResult(
                tool_call_id="call",
                status="denied",
                content="denied",
                error=ToolError(code="POLICY_DENIED", message="No command permission"),
            )
        )
        record = await store.get(run_id)
    if consumer == "g5":
        assert workspace is not None
        await execution.begin_model_retry(request=request_for(execution.state))
        await execution.record_model_response(
            tool_response(name="shell", arguments={"command": "printf done"}),
            None,
            disposition=ToolsDisposition(),
        )
        assert execution.state.execution.tool_batch is not None
        call = execution.state.execution.tool_batch.calls[0]
        await execution.begin_tool(
            call_index=0, tool_call_id=call.id, tool_name="shell"
        )
        await execution.begin_tool_execution(
            canonical_arguments={"command": "printf done"}
        )
        assert execution.state.execution.active_operation is not None
        observer = execution.execution_observer(
            execution.state.execution.active_operation.attempt_id
        )
        from miniagent.environments import Command

        with execution_scope(observer):
            await workspace.execute(Command(argv=("/bin/sh", "-c", "printf done")))
        record = await store.get(run_id)
    boundary = PageBoundary(
        run_id=run_id,
        as_of_sequence=record.last_event_sequence,
        latest_snapshot_version=record.latest_snapshot_version,
        run_status=record.state.lifecycle.status,
        read_at=datetime.now(UTC),
    )
    proof = None
    if consumer == "g1":
        plan = summary_plan()
        plan = replace(
            plan,
            state=plan.state.model_copy(
                update={
                    "identity": plan.state.identity.model_copy(
                        update={"run_id": run_id}
                    ),
                    "definition": plan.state.definition.model_copy(
                        update={"config": record.state.definition.config}
                    ),
                }
            ),
            source_event_sequence=record.last_event_sequence,
        )
        requests = await store.list_events(
            run_id,
            through_sequence=record.last_event_sequence,
            types=(RunEventType.MODEL_REQUESTED,),
            limit=2,
        )
        proof = summary_response(
            verified_evidence=[
                {
                    "claim": json.dumps("qwen"),
                    "field_path": ["provider"],
                    "value": "qwen",
                    "source": {
                        "kind": "event",
                        "run_id": str(run_id),
                        "event_id": str(event.event_id),
                    },
                }
                for event in requests
            ]
        )
        del requests
    gc.collect()
    patch = pytest.MonkeyPatch()
    patch.setattr(sqlite3, "connect", traced)
    tracemalloc.start()
    started = time.perf_counter()
    oracle = "accepted"
    try:
        if consumer == "g1":
            assert proof is not None
            result = await validate_summary(proof, plan, store)
        elif consumer == "g2":
            result = await validate_compaction_record(record, store)
        elif consumer == "g3":
            result = await inspect_resume(
                ResumeConfig(run_id=run_id, database=database, dry_run=True)
            )
            assert isinstance(result, ResumePlan), result
        elif consumer == "g4":

            class ProbeFinished(BaseException):
                pass

            class Provider:
                async def generate(self, request: ModelRequest):
                    raise ProbeFinished

            runtime = AgentRuntime(
                provider=Provider(),
                tools=ToolRegistry([ShellTool(workspace)])
                if workspace
                else ToolRegistry([]),
                run_store=store,
                artifact_store=artifacts,
            )
            try:
                await runtime.resume(record)
            except ProbeFinished:
                result = None
        elif consumer == "g5":
            result = await execution.complete_tool(
                ToolResult(tool_call_id=call.id, status="success", content="done")
            )
        elif consumer == "g6":
            assert workspace is not None
            from miniagent.cli.diagnostic_finalization import finalize_diagnostic

            result = await finalize_diagnostic(
                recorder=RunRecorder.attach(store, record),
                workspace=workspace,
                artifact_store=artifacts,
                deadline=time.monotonic() + 30,
            )
            assert result.status == "empty", result
        elif consumer == "g7":
            with connect(database) as connection:
                ids = tuple(
                    row[0]
                    for row in connection.execute(
                        "SELECT DISTINCT span_id FROM run_events WHERE span_id IS NOT NULL LIMIT 200"
                    )
                )
            result = read_scope_facts(
                database,
                boundary,
                ids,
                budget=ReadBudget(deadline=time.monotonic() + 30),
                connect=traced,
            )
            assert all(
                item.event is None
                for scope in result.scopes.values()
                for item in scope.events
            )
            project_spans(result, boundary)
            page = read_events(
                database,
                boundary,
                EventFilters(),
                budget=ReadBudget(deadline=time.monotonic() + 30),
                connect=traced,
            )
            assert sum(item.event is not None for item in page.items) <= 16
            del page
            with traced(database) as connection:
                keys = tuple(
                    (row[0], row[1])
                    for row in connection.execute(
                        "SELECT step_number,json_extract(payload_json,'$.tool_call_id') FROM run_events WHERE run_id=? AND sequence<=? AND type='tool_start' LIMIT 200",
                        (str(run_id), record.last_event_sequence),
                    )
                )
                target = connection.execute(
                    "SELECT event_id FROM run_events WHERE run_id=? AND sequence<=? ORDER BY ABS(sequence-?) LIMIT 1",
                    (
                        str(run_id),
                        record.last_event_sequence,
                        record.last_event_sequence // 2,
                    ),
                ).fetchone()[0]
            tools = read_tool_facts(
                database,
                boundary,
                keys,
                budget=ReadBudget(deadline=time.monotonic() + 30),
                connect=traced,
            )
            assert sum(item.event is not None for item in tools.events) <= 16
            del tools
            located = locate_event(
                database,
                boundary,
                UUID(target),
                budget=ReadBudget(deadline=time.monotonic() + 30),
                connect=traced,
            )
            assert sum(item.event is not None for item in located.items) <= 16
            del located
            compactions = read_compaction_sources(
                database,
                boundary,
                version=record.state.context.current_version or 1,
                budget=ReadBudget(deadline=time.monotonic() + 30),
                connect=traced,
            )
            assert (
                sum(
                    item.event is not None and item.type != "compaction_completed"
                    for item in (
                        *compactions.events,
                        *compactions.source_events.values(),
                    )
                )
                <= 16
            )
            del compactions
        elif consumer == "g8":
            step = max(1, record.state.execution.steps_started - 1)
            result = await store.list_tool_results(
                run_id, step_number=step, through_sequence=record.last_event_sequence
            )
            assert all(
                len(fact.result.model_dump_json().encode()) <= 65536 for fact in result
            )
        else:
            raise ValueError(consumer)
        wall = time.perf_counter() - started
        retained = retained_events(result)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        patch.undo()
        tracemalloc.stop()
    assert retained == 0, f"{consumer} retains full event payloads"
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        **metadata,
        "consumer": consumer,
        "boundary": record.last_event_sequence,
        "oracle": oracle,
        "wall_seconds": wall,
        "rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "tracemalloc_peak_bytes": peak,
        "retained_full_events": retained,
        "max_live_events": live_event_max[0],
        "point_probe_cadence": 16,
        "queries": queries,
    }


async def verify(root: Path) -> dict:
    metadata = json.loads((root / "history.json").read_text())
    artifacts = FileArtifactStore(root / "artifacts")
    cursor = None
    count = 0
    while True:
        page = await artifacts.list_records_for_run(
            UUID(metadata["run_id"]), cursor=cursor, limit=100
        )
        for record in page.records:
            await artifacts.read(record.reference())
            count += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    digest = hashlib.sha256()
    with sqlite3.connect(root / "runs.db") as connection:
        for (payload,) in connection.execute(
            "SELECT payload_json FROM run_events ORDER BY sequence"
        ):
            digest.update(payload.encode())
        actual = connection.execute("SELECT COUNT(*) FROM run_events").fetchone()[0]
    assert actual == metadata["count"]
    return {
        "verified_artifacts": count,
        "history_sha256": digest.hexdigest(),
        "event_count": actual,
    }


async def export_history(root: Path) -> dict:
    from miniagent.cli.run_definition import parse_application_definition
    from miniagent.cli.trajectory import try_export_trajectory
    from miniagent.storage.run_lock import RunLock

    metadata = json.loads((root / "history.json").read_text())
    run_id = UUID(metadata["run_id"])
    store = SQLiteRunStore(root / "runs.db")
    artifacts = FileArtifactStore(root / "artifacts")
    with RunLock(store.path, run_id):
        record = await store.get(run_id)
        assert (
            record.recovery_context is not None
            and record.recovery_context.application is not None
        )
        definition = parse_application_definition(
            record.recovery_context.application, record.state.definition
        )
        outcome = await try_export_trajectory(
            enabled=True,
            run_id=run_id,
            run_store=store,
            artifact_store=artifacts,
            definition=definition,
            timeout_seconds=30,
            cleanup_deadline=None,
        )
    assert outcome.status == "exported", outcome
    return {"ref": outcome.ref.model_dump(mode="json")}


if __name__ == "__main__":
    root, consumer = Path(sys.argv[1]), sys.argv[2]
    print(
        json.dumps(
            asyncio.run(
                verify(root)
                if consumer == "verify"
                else export_history(root)
                if consumer == "export"
                else consume(root, consumer)
            )
        )
    )
