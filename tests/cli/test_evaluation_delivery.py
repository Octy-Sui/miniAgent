from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from miniagent.artifacts import ArtifactWrite
from miniagent.cli.application import Application
from miniagent.cli.candidate_submission import (
    WorkspaceCandidateEvaluator,
)
from miniagent.cli.rendering import print_application_result
from miniagent.evaluation import EvaluationResult
from miniagent.runtime import CancellationToken, RunStatus


async def test_delivery_reuses_validated_patch_after_workspace_changes(delivery_case):
    deps, submission = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    expected = (await deps.workspace.export_patch()).content.encode()

    class MutatingEvaluator:
        async def evaluate(self, request):
            (deps.workspace.root / "tracked.txt").write_text("later change\n")
            return EvaluationResult(passed=True)

    app = Application(
        replace(
            deps, evaluator=WorkspaceCandidateEvaluator(submission, MutatingEvaluator())
        )
    )
    result = await app.run(CancellationToken())
    assert result.run_result.status is RunStatus.SUCCEEDED
    assert len(result.artifacts) == 1
    assert await deps.artifact_store.read(result.artifacts[0]) == expected
    assert not deps.workspace.root.exists()


@pytest.mark.parametrize(("damage_patch",), [(False,), (True,)])
async def test_collection_failure_only_returns_verified_patch(
    delivery_case, damage_patch
):
    deps, submission = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")

    class CorruptingEvaluator:
        async def evaluate(self, request):
            ref = await deps.artifact_store.put(
                ArtifactWrite(run_id=request.run_id, type="evaluation_report"),
                b"report",
            )
            Path(urlparse(ref.uri).path).write_bytes(b"corrupt")
            if damage_patch:
                Path(urlparse(request.candidate.patch.uri).path).write_bytes(b"corrupt")
            return EvaluationResult(passed=True, artifacts=[ref])

    result = await Application(
        replace(
            deps,
            evaluator=WorkspaceCandidateEvaluator(submission, CorruptingEvaluator()),
        )
    ).run(CancellationToken())
    assert result.run_result.status is RunStatus.SUCCEEDED
    assert [ref.type for ref in result.artifacts] == (
        [] if damage_patch else ["workspace_patch"]
    )
    assert any(
        error.stage == "artifact_collection" for error in result.finalization_errors
    )
    assert deps.workspace.root.exists()


async def test_empty_local_success_destroys_workspace_without_finalization_error(
    delivery_case,
):
    deps, _ = delivery_case
    result = await Application(deps).run(CancellationToken())
    assert result.artifacts == ()
    assert result.finalization_errors == ()
    assert not deps.workspace.root.exists()


async def test_renderer_finds_patch_by_type(delivery_case, capsys):
    deps, _ = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    report = await deps.artifact_store.put(
        ArtifactWrite(run_id=deps.run_id, type="evaluation_report"), b"hidden"
    )
    result = await Application(deps).run(CancellationToken())
    patch = next(ref for ref in result.artifacts if ref.type == "workspace_patch")
    result = result.model_copy(update={"artifacts": (report, patch)})
    print_application_result(result, failure_exit_code=1)
    assert f"Patch: {patch.uri}" in capsys.readouterr().out
