from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactRef
from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    ManagedExecutionError,
    current_execution_observer,
)
from miniagent.evaluation import EvaluationError, EvaluationResult
from miniagent.evaluation.contracts import EvaluationCheck
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime import AgentRuntime, RunEventType
from miniagent.runtime.execution import RunExecution, current_evaluation_observer
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import CandidateDisposition
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.tools.registry import ToolRegistry
from tests.support.resume_helpers import StoreBoundary
from tests.support.runtime.request_resume import started
from tests.support.runtime.runtime import QueueProvider


@pytest.mark.parametrize(
    ("passed", "code"),
    [(True, None), (False, "TASK_UNRESOLVED"), (False, "EVALUATOR_FAILED")],
)
async def test_committed_evaluation_result_finishes_without_evaluator(
    tmp_path, passed, code
):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="original candidate"),
            finish_reason=FinishReason.STOP,
        ),
        None,
        disposition=CandidateDisposition(text="original candidate"),
    )
    await execution.begin_evaluation("original candidate")
    ref = ArtifactRef(
        artifact_id=uuid4(),
        uri="file:///controlled/report.json",
        type="evaluation_report",
        sha256="a" * 64,
    )
    supplied = EvaluationResult(
        passed=passed,
        output="verified output" if passed else None,
        error=EvaluationError(code=code, message="trusted failure") if code else None,
        checks=[
            EvaluationCheck(
                name="independent check",
                passed=passed,
                summary="full check",
                artifact=ref,
                duration_ms=7,
            )
        ],
        artifacts=[ref],
    )
    observer = execution.evaluation_observer()
    assert observer.progress.result is None
    await execution.record_evaluation_result(supplied)
    assert observer.progress.result == supplied
    record = await store.get(execution.state.identity.run_id)
    assert record.state.execution.evaluation.result == supplied
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )

    class MustNotEvaluate:
        async def evaluate(self, request):
            raise AssertionError("Committed result must not rerun evaluator")

    result = await AgentRuntime(
        provider=QueueProvider([]),
        tools=ToolRegistry([]),
        run_store=store,
        evaluator=MustNotEvaluate(),
    )._drive(execution)
    events = await store.list_events(result.run_id)
    assert sum(event.type == RunEventType.EVALUATION_COMPLETED for event in events) == 1
    assert sum(event.type == RunEventType.RUN_FINISHED for event in events) == 1
    completed = next(
        event for event in events if event.type == RunEventType.EVALUATION_COMPLETED
    )
    assert completed.artifacts == [ref]
    assert result.output == ("verified output" if passed else None)
    assert (
        await store.get(result.run_id)
    ).state.execution.evaluation.result == supplied


@pytest.mark.parametrize(
    ("error",),
    [
        (ManagedExecutionError("unconfirmed group"),),
        (
            RunPersistenceError(
                run_id=uuid4(), operation="observer", last_confirmed_sequence=1
            ),
        ),
    ],
)
async def test_evaluator_control_error_never_becomes_business_failure(tmp_path, error):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="candidate"),
            finish_reason=FinishReason.STOP,
        ),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )

    class FailingObserverEvaluator:
        async def evaluate(self, request):
            raise error

    with pytest.raises(type(error)):
        await AgentRuntime(
            provider=QueueProvider([]),
            tools=ToolRegistry([]),
            run_store=store,
            evaluator=FailingObserverEvaluator(),
        )._drive(execution)
    assert current_evaluation_observer() is None
    assert not any(
        event.type == RunEventType.RUN_FINISHED
        for event in await store.list_events(execution.state.identity.run_id)
    )


async def test_evaluator_cannot_commit_result_with_unfinished_identified_execution(
    tmp_path,
):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="candidate"),
            finish_reason=FinishReason.STOP,
        ),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )

    class UnfinishedEvaluator:
        async def evaluate(self, request):
            observer = current_execution_observer()
            assert observer is not None
            await observer.identified(
                HostProcessIdentity(
                    role="harness",
                    attempt_id=observer.attempt_id,
                    execution_id=uuid4(),
                    pid=123,
                    create_time=1,
                    host_boot_id="controlled",
                    pgid=123,
                    sid=123,
                )
            )
            return EvaluationResult(passed=True)

    with pytest.raises(ManagedExecutionError, match="end"):
        await AgentRuntime(
            provider=QueueProvider([]),
            tools=ToolRegistry([]),
            run_store=store,
            evaluator=UnfinishedEvaluator(),
        )._drive(execution)
    assert (
        await store.get(execution.state.identity.run_id)
    ).state.execution.evaluation.result is None


@pytest.mark.parametrize(
    ("action",), [("applied_then_error",), ("not_dispatched_then_error",)]
)
async def test_real_observer_commit_uncertainty_stops_evaluation(tmp_path, action):
    store, execution = await started(tmp_path)
    await execution.record_model_response(
        ModelResponse(
            message=AssistantMessage(content="candidate"),
            finish_reason=FinishReason.STOP,
        ),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    record = await store.get(execution.state.identity.run_id)
    boundary = StoreBoundary(
        store, action=action, event_type=RunEventType.OPERATION_EXECUTION_IDENTIFIED
    )
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(boundary, record), [])
    )
    calls = []

    class ObserverEvaluator:
        async def evaluate(self, request):
            observer = current_execution_observer()
            assert observer is not None
            await observer.identified(
                HostProcessIdentity(
                    role="harness",
                    attempt_id=observer.attempt_id,
                    execution_id=uuid4(),
                    pid=123,
                    create_time=1,
                    host_boot_id="controlled",
                    pgid=123,
                    sid=123,
                )
            )
            calls.append("forbidden successor")
            return EvaluationResult(passed=True)

    with pytest.raises(RunPersistenceError):
        await AgentRuntime(
            provider=QueueProvider([]),
            tools=ToolRegistry([]),
            run_store=boundary,
            evaluator=ObserverEvaluator(),
        )._drive(execution)
    assert boundary.fired and calls == []
    assert current_evaluation_observer() is None
    assert not any(
        event.type == RunEventType.RUN_FINISHED
        for event in await store.list_events(record.state.identity.run_id)
    )
