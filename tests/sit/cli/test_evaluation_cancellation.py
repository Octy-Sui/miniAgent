import asyncio
import json
from dataclasses import replace
from time import monotonic

import pytest

from miniagent.cli.application import Application
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
    WorkspaceCandidateEvaluator,
)
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.runtime import (
    CancellationToken,
    RunEventType,
    RunRecoveryContext,
    RunStatus,
    TerminationReason,
)
from miniagent.runtime.recording import RunPersistenceError


async def test_cancel_during_harness_collects_diagnostics_without_completed_event(
    delivery_case, evaluation_case
):
    deps, submission = delivery_case
    evaluator, _, _ = evaluation_case
    evaluator.artifact_store = deps.artifact_store
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    root = evaluator.executable.parent
    (root / "harness-scenario.json").write_text(json.dumps({"hang": True}))
    token = CancellationToken()
    running = asyncio.create_task(
        Application(
            replace(deps, evaluator=WorkspaceCandidateEvaluator(submission, evaluator))
        ).run(token)
    )
    while not (root / "harness-started").exists() and not running.done():
        await asyncio.sleep(0.01)
    token.cancel()
    result = await running
    assert result.run_result.status is RunStatus.CANCELLED
    assert result.run_result.reason is TerminationReason.CANCEL_REQUESTED
    assert "evaluation_summary" in {ref.type for ref in result.artifacts}
    assert deps.workspace.root.exists()
    events = await deps.run_store.list_events(deps.run_id)
    assert RunEventType.EVALUATION_COMPLETED not in {event.type for event in events}


async def test_cancel_before_attempt_preserves_run_and_workspace(
    delivery_case, evaluation_case, export_workspace, tmp_path
):
    deps, _ = delivery_case
    evaluator, _, _ = evaluation_case
    (tmp_path / "mode").write_text("hang")
    submission = CandidateSubmission(
        export_workspace,
        deps.artifact_store,
        timeout_seconds=3,
        cleanup_timeout_seconds=0.3,
    )
    deps = replace(
        deps,
        workspace=export_workspace,
        run_id=export_workspace.run_id,
        candidate_submission=submission,
        evaluator=WorkspaceCandidateEvaluator(submission, evaluator),
        recovery_context=RunRecoveryContext(
            workspace_id=export_workspace.workspace_id,
            workspace_spec=persist_workspace_spec(export_workspace.spec),
        ),
    )
    token = CancellationToken()
    started = monotonic()
    running = asyncio.create_task(Application(deps).run(token))
    while not (tmp_path / "cleanup-started").exists() and not running.done():
        await asyncio.sleep(0.01)
    token.cancel()
    result = await running
    assert result.run_result.status is RunStatus.CANCELLED
    assert submission.result.status == "cancelled"
    assert submission.result.submission_id is not None
    assert any(error.stage == "patch_export" for error in result.finalization_errors)
    assert not (tmp_path / "harness-call.json").exists()
    assert export_workspace.state.value == "active"
    assert monotonic() - started < 6


@pytest.mark.parametrize(("window",), [("stdout",), ("summary",)])
async def test_cancellation_during_evidence_collection_marks_summary_cancelled(
    delivery_case, evaluation_case, window
):
    deps, submission = delivery_case
    evaluator, _, _ = evaluation_case
    evaluator.artifact_store = deps.artifact_store
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"log_size": 8 * 1024 * 1024})
    )
    token = CancellationToken()
    running = asyncio.create_task(
        Application(
            replace(deps, evaluator=WorkspaceCandidateEvaluator(submission, evaluator))
        ).run(token)
    )
    reached = False
    directory = deps.artifact_store.manifest_root / deps.run_id.hex
    while not running.done():
        stdout_ready = any(
            json.loads(path.read_bytes())["type"] == "evaluation_stdout"
            for path in directory.glob("*.json")
        )
        summaries = list(
            evaluator.settings.swebench_work_root.rglob("evaluation-summary.json")
        )
        summary_ready = any(
            json.loads(path.read_bytes()).get("verdict")
            in {"passed", "report_resolved"}
            for path in summaries
        )
        if stdout_ready if window == "stdout" else summary_ready:
            reached = True
            token.cancel()
            break
        await asyncio.sleep(0)
    result = await running
    assert reached, "Public stdout publication window was not reached"
    assert result.run_result.status is RunStatus.CANCELLED
    summary_ref = next(
        ref for ref in result.artifacts if ref.type == "evaluation_summary"
    )
    summary = json.loads(await deps.artifact_store.read(summary_ref))
    if window == "stdout":
        assert summary["verdict"] == "cancelled"
    else:
        assert summary["scope"] == "evaluation_evidence_snapshot"
        assert summary["verdict"] != "passed"
        assert summary["terminal_status_source"] == "Run"


async def test_published_patch_is_recovered_after_submission_cancellation(
    delivery_case, export_workspace, tmp_path
):
    deps, _ = delivery_case
    (tmp_path / "mode").write_text("large")
    submission = CandidateSubmission(export_workspace, deps.artifact_store)
    pending = asyncio.create_task(submission.submit(kind="candidate"))
    manifests = deps.artifact_store.manifest_root / export_workspace.run_id.hex
    observed = False
    while not pending.done():
        if list(manifests.glob("*.json")):
            observed = True
            pending.cancel()
            break
        await asyncio.sleep(0)
    try:
        await pending
    except asyncio.CancelledError:
        pass
    if not observed or submission.result.patch_ref is not None:
        pytest.skip("Exact publication-before-ref cancellation window was not observed")
    assert submission.result.status == "cancelled"
    calls_before = (tmp_path / "export-calls.jsonl").read_bytes()
    token = CancellationToken()
    token.cancel()
    deps = replace(
        deps,
        workspace=export_workspace,
        run_id=export_workspace.run_id,
        candidate_submission=submission,
        recovery_context=RunRecoveryContext(
            workspace_id=export_workspace.workspace_id,
            workspace_spec=persist_workspace_spec(export_workspace.spec),
        ),
    )
    result = await Application(deps).run(token)
    assert result.run_result.status is RunStatus.CANCELLED
    assert [ref.type for ref in result.artifacts] == ["workspace_patch"]
    record = await deps.artifact_store.get_record(result.artifacts[0])
    assert record.metadata["submission_id"] == str(submission.result.submission_id)
    assert (tmp_path / "export-calls.jsonl").read_bytes() == calls_before


async def test_persistence_uncertainty_skips_diagnostic_finalization(delivery_case):
    deps, submission = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("must remain unexported\n")

    class UncertainEvaluator:
        async def evaluate(self, request):
            raise RunPersistenceError(
                run_id=request.run_id, operation="evaluation", last_confirmed_sequence=1
            )

    with pytest.raises(RunPersistenceError):
        await Application(replace(deps, evaluator=UncertainEvaluator())).run(
            CancellationToken()
        )
    assert submission.result.status == "not_attempted"
    assert deps.workspace.root.exists()
