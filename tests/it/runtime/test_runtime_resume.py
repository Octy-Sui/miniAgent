from datetime import UTC, datetime
from uuid import uuid4

import pytest

from miniagent.evaluation import EvaluationResult
from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.events import (
    NewRunEvent,
    ResumeStarted,
    RunSuspended,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import (
    CandidateDisposition,
    InterruptionInfo,
    TerminateDisposition,
    TerminationDecision,
)
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.result import TerminationReason
from miniagent.runtime.transitions import begin_resume, suspend
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolStatus
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.request_resume import started
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    agent,
    final_response,
)
from tests.support.runtime.runtime_resume import completed_protocol
from tests.support.runtime.tool_batch_resume import prefix


@pytest.mark.parametrize(("phase",), [("request",), ("candidate",), ("terminate",)])
async def test_resume_attaches_without_create_or_run_started(
    tmp_path, phase, runtime_persistence
):
    store, execution = await started(tmp_path)
    if phase != "request":
        disposition = (
            CandidateDisposition(text="candidate")
            if phase == "candidate"
            else TerminateDisposition(
                decision=TerminationDecision(
                    status="failed", reason=TerminationReason.MODEL_FAILED
                )
            )
        )
        await execution.record_model_response(
            ModelResponse(
                message=AssistantMessage(content="candidate"),
                finish_reason=FinishReason.STOP,
            ),
            None,
            disposition=disposition,
        )
    record = await completed_protocol(store, execution)
    before = await store.list_events(record.state.identity.run_id)
    provider = QueueProvider(
        [final_response("candidate")] if phase == "request" else []
    )
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=provider,
        tools=ToolRegistry([]),
        run_store=store,
    ).resume(record)
    events = await store.list_events(result.run_id)
    assert result.run_id == record.state.identity.run_id
    assert sum(event.type == RunEventType.RUN_STARTED for event in events) == 1
    assert sum(event.type == RunEventType.STEP_STARTED for event in events) == 1
    state = (await store.get(result.run_id)).state
    assert [message.role for message in state.conversation.messages].count(
        "system"
    ) == 1
    assert [message.role for message in state.conversation.messages].count("user") == 1
    assert events[: len(before)] == before


@pytest.mark.parametrize(
    ("invalid",),
    [("stale",), ("recovering",), ("suspended",), ("running_without_completed",)],
)
async def test_resume_rejects_stale_or_recovering_record(
    tmp_path, invalid, runtime_persistence
):
    store, execution = await started(tmp_path)
    record = await store.get(execution.state.identity.run_id)
    if invalid == "stale":
        record = await completed_protocol(store, execution)
        journal = RunJournal.attach(RunRecorder.attach(store, record), [])
        next_state = begin_resume(
            record.state, attempt_id=uuid4(), source_sequence=record.last_event_sequence
        )
        session = next_state.recovery.session
        assert session is not None
        await journal.start_resume(
            next_state,
            [
                NewRunEvent(
                    run_id=record.state.identity.run_id,
                    payload=ResumeStarted(
                        resume_attempt_id=session.attempt_id,
                        source_sequence=session.source_sequence,
                        business_status=session.business_status,
                        phase=session.phase_at_start,
                    ),
                )
            ],
        )
    elif invalid == "recovering":
        state = begin_resume(
            record.state, attempt_id=uuid4(), source_sequence=record.last_event_sequence
        )
        session = state.recovery.session
        assert session is not None
        result = await execution.journal.start_resume(
            state,
            [
                NewRunEvent(
                    run_id=record.state.identity.run_id,
                    payload=ResumeStarted(
                        resume_attempt_id=session.attempt_id,
                        source_sequence=session.source_sequence,
                        business_status=session.business_status,
                        phase=session.phase_at_start,
                    ),
                )
            ],
        )
        record = result.record
    elif invalid == "suspended":
        state = suspend(
            record.state,
            InterruptionInfo(signal_number=2, requested_at=datetime.now(UTC)),
        )
        assert (
            state.recovery.suspension is not None and state.execution.phase is not None
        )
        result = await execution.journal.suspend_run(
            state,
            [
                NewRunEvent(
                    run_id=record.state.identity.run_id,
                    payload=RunSuspended(
                        interruption=state.recovery.suspension.interruption,
                        business_status=state.recovery.suspension.business_status,
                        phase=state.execution.phase,
                    ),
                )
            ],
        )
        record = result.record
    provider = QueueProvider([])
    with pytest.raises(ValueError, match="resume|Resume|recover|stale|completed"):
        await AgentRuntime(
            artifact_store=runtime_persistence.artifacts,
            provider=provider,
            tools=ToolRegistry([]),
            run_store=store,
        ).resume(record)
    assert provider.requests == []


@pytest.mark.parametrize(("complete_batch",), [(False,), (True,)])
async def test_resume_dispatches_original_tool_and_complete_step_phases(
    tmp_path, complete_batch, runtime_persistence
):

    store, execution, tool = await prefix(
        tmp_path, ToolStatus.SUCCESS, complete_batch=complete_batch
    )
    record = await completed_protocol(store, execution)
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([]),
        tools=ToolRegistry([tool]),
        run_store=store,
        policy_engine=AllowPolicyEngine(),
    ).resume(record)
    assert result.reason is TerminationReason.MAX_STEPS_REACHED
    assert (tmp_path / "effects").read_text().splitlines() == ["c0", "c1", "c2"]


async def test_resume_restarts_uncommitted_evaluation_without_completing_step_twice(
    tmp_path,
    runtime_persistence,
):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        final_response("candidate"),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    await execution.begin_evaluation("candidate")
    record = await completed_protocol(store, execution)
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([]),
        tools=ToolRegistry([]),
        run_store=store,
    ).resume(record)
    assert result.output == "candidate"
    events = await store.list_events(result.run_id)
    assert sum(event.type == RunEventType.STEP_COMPLETED for event in events) == 1


async def test_resume_prepares_existing_created_record_without_run_started(
    tmp_path, runtime_persistence
):
    store = SQLiteRunStore(tmp_path / "created.sqlite3")
    await store.initialize()
    spec = agent()
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=spec,
        config=RuntimeConfig(),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    journal = RunJournal(
        RunRecorder(run_id=state.identity.run_id, store=store, recovery_context=None)
    )
    await journal.create(state)
    execution = RunExecution.attach(await store.get(state.identity.run_id), journal)
    record = await completed_protocol(store, execution)
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([final_response()]),
        tools=ToolRegistry([]),
        run_store=store,
    ).resume(record)
    assert result.output == "finished"
    events = await store.list_events(result.run_id)
    assert not any(event.type == RunEventType.RUN_STARTED for event in events)
    assert sum(event.type == RunEventType.STEP_STARTED for event in events) == 1


async def test_resume_terminates_from_complete_evaluation_result(
    tmp_path, runtime_persistence
):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        final_response("candidate"),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    await execution.begin_evaluation("candidate")
    supplied = EvaluationResult(passed=True, output="verified")
    await execution.record_evaluation_result(supplied)
    record = await completed_protocol(store, execution)
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([]),
        tools=ToolRegistry([]),
        run_store=store,
    ).resume(record)
    assert result.output == "verified"
    assert (
        await store.get(result.run_id)
    ).state.execution.evaluation.result == supplied
