import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
    WorkspaceCandidateEvaluator,
)
from miniagent.environments import (
    Command,
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
)
from miniagent.evaluation import EvaluationRequest, EvaluationResult
from miniagent.evaluation.contracts import CandidateOutput, EvaluationPlan


@pytest.fixture
async def submission_case(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    await asyncio.to_thread(
        subprocess.run, ["git", "init", "--quiet", str(source)], check=True
    )
    (source / "tracked.txt").write_text("original\n")
    await asyncio.to_thread(
        subprocess.run, ["git", "-C", str(source), "add", "."], check=True
    )
    await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        check=True,
    )
    store = FileArtifactStore(tmp_path / "artifacts")
    workspace = await LocalWorkspaceBackend(
        root=tmp_path / "workspaces", artifact_store=store
    ).create(
        LocalWorkspaceSpec(run_id=uuid4(), options=LocalOptions(source_dir=source))
    )
    return workspace, store


class RecordingEvaluator:
    def __init__(self):
        self.requests = []

    async def evaluate(self, request):
        self.requests.append(request)
        return EvaluationResult(passed=True)


async def test_submit_once_preserves_request_and_bytes(submission_case):
    workspace, store = submission_case
    (workspace.root / "tracked.txt").write_text("changed\n")
    expected = (await workspace.export_patch()).content.encode()
    submission = CandidateSubmission(workspace, store)
    downstream = RecordingEvaluator()
    request = EvaluationRequest(
        run_id=workspace.run_id,
        task="issue",
        candidate=CandidateOutput(text="candidate"),
        plan=EvaluationPlan(target_commands=[Command(argv=["true"])]),
    )
    assert (
        await WorkspaceCandidateEvaluator(submission, downstream).evaluate(request)
    ).passed is True
    received = downstream.requests[0]
    assert received.run_id == request.run_id
    assert received.task == request.task
    assert received.plan == request.plan
    assert received.candidate.text == request.candidate.text
    assert await store.read(received.candidate.patch) == expected
    assert submission.result.patch_ref == received.candidate.patch
    (workspace.root / "tracked.txt").write_text("different\n")
    first = submission.result
    assert await submission.submit(kind="candidate") == first
    records = await store.list_for_run(workspace.run_id)
    assert len(records) == 1
    assert records[0].metadata["submission_id"] == str(first.submission_id)
    assert records[0].metadata["base_revision"] == workspace.base_revision
    assert await store.read(first.patch_ref) == expected


async def test_empty_patch_fails_without_calling_downstream(submission_case):
    workspace, store = submission_case
    submission = CandidateSubmission(workspace, store)
    downstream = RecordingEvaluator()
    result = await WorkspaceCandidateEvaluator(submission, downstream).evaluate(
        EvaluationRequest(
            run_id=workspace.run_id,
            task="issue",
            candidate=CandidateOutput(text="done"),
        )
    )
    assert result.passed is False
    assert result.error is not None
    assert result.error.code == "EMPTY_PATCH"
    assert downstream.requests == []
    first = submission.result
    (workspace.root / "tracked.txt").write_text("changed\n")
    assert await submission.submit(kind="candidate") == first
    assert await store.list_for_run(workspace.run_id) == []


async def test_diagnostic_patch_has_submission_without_attempt(submission_case):
    workspace, store = submission_case
    (workspace.root / "tracked.txt").write_text("changed\n")
    result = await CandidateSubmission(workspace, store).submit(kind="diagnostic")
    record = await store.get_record(result.patch_ref)
    assert record.metadata["kind"] == "diagnostic"
    assert "attempt_id" not in record.metadata
    assert result.submission_id is not None


async def test_export_failure_is_not_retried(submission_case):
    workspace, store = submission_case
    await workspace.destroy()
    submission = CandidateSubmission(workspace, store)
    first = await submission.submit(kind="candidate")
    assert first.error is not None
    assert first.error.code == "PATCH_EXPORT_FAILED"
    assert await submission.submit(kind="diagnostic") == first


async def test_registration_failure_keeps_submission_identity(
    submission_case, tmp_path
):
    workspace, _ = submission_case
    (workspace.root / "tracked.txt").write_text("changed\n")
    root = tmp_path / "blocked"
    root.write_text("not a directory")
    submission = CandidateSubmission(workspace, FileArtifactStore(root))
    first = await submission.submit(kind="candidate")
    assert first.error is not None
    assert first.error.code == "ARTIFACT_REGISTRATION_FAILED"
    assert first.patch_ref is None
    assert await submission.submit(kind="candidate") == first


async def test_cancelled_submission_is_not_retried(submission_case):
    workspace, store = submission_case
    executing = asyncio.create_task(
        workspace.execute(Command(argv=["/bin/sh", "-c", "touch busy; sleep 10"]))
    )
    while not (workspace.root / "busy").exists():
        await asyncio.sleep(0.01)
    submission = CandidateSubmission(
        workspace, store, timeout_seconds=5, cleanup_timeout_seconds=0.2
    )
    submitting = asyncio.create_task(submission.submit(kind="candidate"))
    await asyncio.sleep(0)
    submitting.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await submitting
        assert submission.result.status == "cancelled"
        first = submission.result
        assert first.submission_id is not None
        assert await submission.submit(kind="diagnostic") == first
    finally:
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing


async def test_cleanup_failure_maps_to_submission_error(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("error")
    submission = CandidateSubmission(
        export_workspace, FileArtifactStore(tmp_path / "artifacts")
    )
    result = await submission.submit(kind="candidate")
    assert result.error is not None
    assert result.error.code == "PATCH_EXPORT_CLEANUP_FAILED"


async def test_nonempty_whitespace_patch_is_submitted(export_workspace, tmp_path):
    (tmp_path / "mode").write_text("whitespace")
    store = FileArtifactStore(tmp_path / "artifacts")
    result = await CandidateSubmission(export_workspace, store).submit(kind="candidate")
    assert result.status == "submitted"
    assert result.patch_ref is not None
    assert await store.read(result.patch_ref) == b" "


async def test_submission_timeout_includes_export_lock_wait(submission_case):
    workspace, store = submission_case
    executing = asyncio.create_task(
        workspace.execute(Command(argv=["/bin/sh", "-c", "touch busy; sleep 10"]))
    )
    while not (workspace.root / "busy").exists():
        await asyncio.sleep(0.01)
    try:
        result = await CandidateSubmission(
            workspace, store, timeout_seconds=0.3, cleanup_timeout_seconds=0.1
        ).submit(kind="candidate")
        assert result.error is not None
        assert result.error.code == "CANDIDATE_SUBMISSION_TIMEOUT"
    finally:
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing
