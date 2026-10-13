from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest

from miniagent.agents import AgentSpec
from miniagent.artifacts import ArtifactWrite
from miniagent.cli.application import Application, ApplicationDependencies
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
    WorkspaceCandidateEvaluator,
)
from miniagent.cli.rendering import print_application_result
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.evaluation import EvaluationResult
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.policy import PolicyContext
from miniagent.runtime import CancellationToken, RunRecoveryContext, RunStatus
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry


class StaticProvider:
    async def generate(self, request):
        return ModelResponse(
            message=AssistantMessage(content="done"),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


@pytest.fixture
async def delivery_case(submission_case, tmp_path):
    workspace, store = submission_case
    journal = SQLiteRunStore(tmp_path / "journal.sqlite3")
    await journal.initialize()
    submission = CandidateSubmission(workspace, store)
    deps = ApplicationDependencies(
        agent=AgentSpec(
            name="test",
            system_prompt="Test.",
            generation=GenerationOptions(max_output_tokens=100),
            model=ModelTarget(provider="test", model="test-model"),
        ),
        task="issue",
        run_id=workspace.run_id,
        provider=StaticProvider(),
        tools=ToolRegistry([]),
        workspace=workspace,
        artifact_store=store,
        pricing=PriceConfig(
            models=[
                ModelPrice(
                    provider="test",
                    model="test-model",
                    input_per_token=0,
                    output_per_token=0,
                )
            ]
        ),
        policy_context=PolicyContext(
            workspace_id=workspace.workspace_id, workspace_kind="local", revision="test"
        ),
        approval_handler=None,
        run_store=journal,
        recovery_context=RunRecoveryContext(
            workspace_id=workspace.workspace_id,
            workspace_spec=persist_workspace_spec(workspace.spec),
        ),
        candidate_submission=submission,
    )
    return deps, submission


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
