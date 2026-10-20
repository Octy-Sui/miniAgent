from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactWrite
from miniagent.cli.candidate_submission import (
    CandidateArtifactInvalid,
    CandidateSubmission,
)
from miniagent.cli.run_definition import CandidateSubmissionState
from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    ManagedExecutionError,
)
from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.runtime import (
    AgentRuntime,
    RunEventType,
    RuntimeConfig,
    create_run_state,
)
from miniagent.runtime.events import CandidateSubmissionStarted, OperationExecutionEnded
from miniagent.runtime.execution import RunExecution, evaluation_scope
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import CandidateDisposition, ComponentEnvelope
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall, ToolStatus
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool
from tests.support.resume_helpers import StoreBoundary
from tests.support.runtime.runtime import (
    AllowPolicyEngine,
    QueueProvider,
    agent,
    final_response,
)
from tests.support.trajectory.fixtures import request_for


async def evaluation_prefix(workspace, tmp_path):
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    await store.initialize()
    spec = agent()
    state = create_run_state(
        run_id=workspace.run_id,
        task="task",
        agent_spec=spec,
        config=RuntimeConfig(),
        messages=(
            SystemMessage(content=spec.system_prompt),
            UserMessage(content="task"),
        ),
    )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(run_id=workspace.run_id, store=store, recovery_context=None)
        ),
    )
    await execution.begin_step(
        provider="test", model="test-model", request=request_for(execution.state)
    )
    await execution.record_model_response(
        final_response("candidate"),
        None,
        disposition=CandidateDisposition(text="candidate"),
    )
    await execution.begin_evaluation("candidate")
    return store, execution


async def test_registered_candidate_reuses_exact_artifact(submission_case, tmp_path):
    """TC-U15-01/02/11: real Git and store component evidence; OS kill is acceptance coverage."""
    workspace, artifacts = submission_case
    (workspace.root / "tracked.txt").write_text("original candidate\n")
    store, execution = await evaluation_prefix(workspace, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        first = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    expected = await artifacts.read(first.patch_ref)
    business_index = (workspace.root / ".git" / "index").read_bytes()
    (workspace.root / "tracked.txt").write_text("later diagnostic\n")
    diagnostic = await CandidateSubmission(workspace, artifacts).submit(
        kind="diagnostic"
    )
    assert diagnostic.patch_ref != first.patch_ref
    record = await store.get(workspace.run_id)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    with evaluation_scope(execution.evaluation_observer()):
        repeated = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    assert repeated.patch_ref == first.patch_ref
    assert await artifacts.read(repeated.patch_ref) == expected
    assert (workspace.root / ".git" / "index").read_bytes() == business_index
    assert (
        (await store.get(workspace.run_id)).state.execution.evaluation.submission
        == record.state.execution.evaluation.submission
    )


@pytest.mark.parametrize(("damage",), [("missing",), ("changed",), ("foreign",)])
async def test_registered_candidate_rejects_invalid_artifact(
    submission_case, tmp_path, damage
):
    """TC-U15-08/09/10: reject missing, changed or foreign registered bytes."""
    workspace, artifacts = submission_case
    (workspace.root / "tracked.txt").write_text("candidate patch\n")
    _store, execution = await evaluation_prefix(workspace, tmp_path)
    with evaluation_scope(execution.evaluation_observer()):
        first = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    envelope = execution.state.execution.evaluation.submission
    assert envelope is not None and first.patch_ref is not None
    state = CandidateSubmissionState.model_validate(envelope.payload)
    if damage == "foreign":
        foreign = await artifacts.put(
            ArtifactWrite(
                run_id=uuid4(),
                type="workspace_patch",
                metadata={
                    "kind": "candidate",
                    "submission_id": str(state.submission_id),
                },
            ),
            await artifacts.read(first.patch_ref),
        )
        state = state.model_copy(update={"patch_ref": foreign})
        execution.state = execution.state.model_copy(
            update={
                "execution": execution.state.execution.model_copy(
                    update={
                        "evaluation": execution.state.execution.evaluation.model_copy(
                            update={
                                "submission": envelope.model_copy(
                                    update={"payload": state.model_dump(mode="json")}
                                )
                            }
                        )
                    }
                )
            }
        )
    else:
        target = Path(urlparse(first.patch_ref.uri).path)
        if damage == "missing":
            target.unlink()
        else:
            target.write_bytes(b"damaged")
    with (
        evaluation_scope(execution.evaluation_observer()),
        pytest.raises(CandidateArtifactInvalid),
    ):
        await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )


async def test_uncommitted_put_is_not_adopted(submission_case, tmp_path):
    """TC-U15-04: G6-U after real put; no adoption of an uncommitted artifact."""
    workspace, artifacts = submission_case
    (workspace.root / "tracked.txt").write_text("first patch\n")
    store, execution = await evaluation_prefix(workspace, tmp_path)
    record = await store.get(workspace.run_id)
    boundary = StoreBoundary(
        store,
        action="not_dispatched_then_error",
        event_type=RunEventType.CANDIDATE_SUBMISSION_COMMITTED,
    )
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(boundary, record), [])
    )
    with (
        evaluation_scope(execution.evaluation_observer()),
        pytest.raises(RunPersistenceError),
    ):
        await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    orphan = (await artifacts.list_for_run(workspace.run_id))[0]
    record = await store.get(workspace.run_id)
    old = CandidateSubmissionState.model_validate(
        record.state.execution.evaluation.submission.payload
    )
    assert old.status == "exporting" and old.patch_ref is None
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    await execution.interrupt_operation(unconfirmed=False)
    (workspace.root / "tracked.txt").write_text("new patch\n")
    with evaluation_scope(execution.evaluation_observer()):
        result = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    assert (
        result.patch_ref is not None
        and execution.state.execution.evaluation.submission is not None
    )
    assert result.patch_ref.artifact_id != orphan.artifact_id
    new = CandidateSubmissionState.model_validate(
        execution.state.execution.evaluation.submission.payload
    )
    assert new.export_attempt_id != old.export_attempt_id
    assert new.temporary_index_path != old.temporary_index_path


@pytest.mark.parametrize(("failed",), [(False,), (True,)])
async def test_committed_empty_or_failed_submission_never_exports_again(
    submission_case, tmp_path, failed
):
    """TC-U15-06/07: committed trusted failure is reused without another export."""
    workspace, artifacts = submission_case
    store, execution = await evaluation_prefix(workspace, tmp_path)
    if failed:
        (workspace.root / "tracked.txt").write_text("patch")
        artifacts.content_root.parent.mkdir(parents=True, exist_ok=True)
        artifacts.content_root.write_text("blocked artifact directory")
    with evaluation_scope(execution.evaluation_observer()):
        original = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    assert original.status == ("failed" if failed else "empty")
    record = await store.get(workspace.run_id)
    if failed:
        artifacts.content_root.unlink()
    (workspace.root / "tracked.txt").write_text("new patch must not be exported")
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    with evaluation_scope(execution.evaluation_observer()):
        repeated = await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    assert repeated == original
    assert await store.get(workspace.run_id) == record


async def test_candidate_and_diagnostic_results_have_independent_identities(
    submission_case,
):
    """TC-U15-11: diagnostic registration cannot replace candidate state."""
    workspace, artifacts = submission_case
    (workspace.root / "tracked.txt").write_text("candidate")
    submission = CandidateSubmission(workspace, artifacts)
    candidate = await submission.submit(kind="candidate")
    (workspace.root / "tracked.txt").write_text("diagnostic")
    diagnostic = await submission.submit(kind="diagnostic")
    assert candidate.submission_id != diagnostic.submission_id
    assert candidate.patch_ref != diagnostic.patch_ref
    assert submission.result == candidate
    assert submission.diagnostic_result == diagnostic


@pytest.mark.parametrize(("name",), [(".git/index",), ("../escape",)])
async def test_controlled_index_rejects_business_index_and_escape(
    submission_case, name
):
    """TC-U15-03: controlled index validation preserves the business index."""
    workspace, _artifacts = submission_case
    index = workspace.root / ".git" / "index"
    original = index.read_bytes()
    with pytest.raises(ValueError, match="Temporary index"):
        await workspace.export_patch(temporary_index_path=str(workspace.root / name))
    assert index.read_bytes() == original


async def test_export_retry_requires_old_chain_end_and_new_index(
    submission_case, tmp_path
):
    """TC-U15-05: missing old-chain end prevents a new export attempt."""
    workspace, artifacts = submission_case
    store, execution = await evaluation_prefix(workspace, tmp_path)
    old_attempt = uuid4()
    state = CandidateSubmissionState(
        candidate_sha256=sha256(b"candidate").hexdigest(),
        submission_id=uuid4(),
        status="exporting",
        export_attempt_id=old_attempt,
        temporary_index_path=str(workspace.root.parent / f"index-{old_attempt.hex}"),
    )
    observer = execution.evaluation_observer()
    assert state.temporary_index_path is not None
    await observer.record(
        CandidateSubmissionStarted(
            attempt_id=old_attempt,
            submission_id=state.submission_id,
            candidate_sha256=state.candidate_sha256,
            temporary_index_path=state.temporary_index_path,
        ),
        component=ComponentEnvelope(
            kind="candidate_submission",
            schema_version=1,
            payload=state.model_dump(mode="json"),
        ),
    )
    await observer.execution_observer(old_attempt).identified(
        HostProcessIdentity(
            role="local_command",
            attempt_id=old_attempt,
            execution_id=uuid4(),
            pid=123,
            create_time=1,
            host_boot_id="controlled",
            pgid=123,
            sid=123,
        )
    )
    record = await store.get(workspace.run_id)
    with evaluation_scope(observer), pytest.raises(ManagedExecutionError, match="end"):
        await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )
    assert await store.get(workspace.run_id) == record


async def test_trusted_timeout_keeps_file_change_and_verified_group_end(
    submission_case, tmp_path, runtime_persistence
):
    """TC-U6-03/04: trusted TIMEOUT retains actual edits and verified execution end."""
    workspace, _artifacts = submission_case
    store = SQLiteRunStore(tmp_path / "timeout.sqlite3")
    await store.initialize()
    tool = ShellTool(workspace, timeout=0.2)
    response = ModelResponse(
        message=AssistantMessage(
            tool_calls=(
                ToolCall(
                    id="timeout",
                    name="shell",
                    arguments={"command": "printf timed > tracked.txt; sleep 10"},
                ),
            )
        ),
        finish_reason=FinishReason.TOOL_CALL,
        usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    result = await AgentRuntime(
        artifact_store=runtime_persistence.artifacts,
        provider=QueueProvider([response]),
        tools=ToolRegistry([tool]),
        run_store=store,
        config=RuntimeConfig(max_steps=1),
        policy_engine=AllowPolicyEngine(),
    ).run(agent(allowed_tools=["shell"]), "task", run_id=workspace.run_id)
    facts = await store.list_tool_results(result.run_id, step_number=1)
    assert facts[0].result.status is ToolStatus.TIMEOUT
    assert (workspace.root / "tracked.txt").read_text() == "timed"
    events = await store.list_events(result.run_id)
    assert any(
        isinstance(event.payload, OperationExecutionEnded)
        and event.payload.evidence.group_ended
        for event in events
    )
