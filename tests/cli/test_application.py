import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from miniagent.agents import AgentSpec
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.application import Application, ApplicationDependencies
from miniagent.environments import (
    Command,
    ExecutionResult,
    LocalOptions,
    LocalWorkspaceSpec,
    Patch,
    WorkspaceCheckpoint,
)
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from miniagent.policy import PolicyContext
from miniagent.runtime import CancellationToken, RunRecoveryContext, RunStatus
from miniagent.storage import SQLiteRunStore
from miniagent.tools.registry import ToolRegistry


class StaticProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            message=AssistantMessage(content="done"),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


class CancellingProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise asyncio.CancelledError


class FinalizationWorkspace:
    def describe_attachment(self):
        raise AssertionError("Attachment metadata is not used by this test")

    def __init__(
        self,
        run_id: UUID,
        *,
        export_error: bool = False,
        destroy_error: bool = False,
    ) -> None:
        self._run_id = run_id
        self.export_error = export_error
        self.destroy_error = destroy_error
        self.destroyed = False

    @property
    def workspace_id(self) -> str:
        return "workspace-1"

    @property
    def run_id(self) -> UUID:
        return self._run_id

    async def execute(self, command: Command) -> ExecutionResult:
        raise AssertionError("not used")

    async def checkpoint(self) -> WorkspaceCheckpoint:
        raise AssertionError("not used")

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None:
        raise AssertionError("not used")

    async def export_patch(
        self,
        *,
        timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
        temporary_index_path: str | None = None,
    ) -> Patch:
        if self.export_error:
            raise RuntimeError("export failed")
        return Patch(content="diff", files=["result.txt"], base_revision="abc")

    async def destroy(self) -> None:
        if self.destroy_error:
            raise RuntimeError("destroy failed")
        self.destroyed = True


def make_artifact_store(tmp_path: Path, *, fail: bool = False) -> FileArtifactStore:
    root = tmp_path / f"artifacts-{uuid4()}"
    if fail:
        root.write_text("not a directory")
    return FileArtifactStore(root)


async def make_application(
    tmp_path: Path,
    workspace: FinalizationWorkspace,
    artifact_store: FileArtifactStore,
    provider: ModelProvider | None = None,
) -> Application:
    run_store = SQLiteRunStore(tmp_path / f"{uuid4()}.sqlite3")
    await run_store.initialize()
    source = tmp_path / f"source-{uuid4()}"
    source.mkdir()
    spec = LocalWorkspaceSpec(
        run_id=workspace.run_id,
        options=LocalOptions(source_dir=source),
    )
    return Application(
        ApplicationDependencies(
            agent=AgentSpec(
                name="test",
                system_prompt="Test.",
                generation=GenerationOptions(max_output_tokens=100),
                model=ModelTarget(provider="test", model="test-model"),
            ),
            task="task",
            run_id=workspace.run_id,
            provider=provider or StaticProvider(),
            tools=ToolRegistry([]),
            workspace=workspace,
            artifact_store=artifact_store,
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
                workspace_id=workspace.workspace_id,
                workspace_kind="local",
                revision="test",
            ),
            approval_handler=None,
            run_store=run_store,
            recovery_context=RunRecoveryContext(
                workspace_id=workspace.workspace_id,
                workspace_spec=persist_workspace_spec(spec),
            ),
        )
    )


@pytest.mark.parametrize(
    ("export_error", "artifact_error", "expected_stage"),
    [
        (True, False, "patch_export"),
        (False, True, "artifact_registration"),
    ],
)
async def test_finalization_failure_preserves_success_and_workspace(
    tmp_path: Path,
    export_error: bool,
    artifact_error: bool,
    expected_stage: str,
) -> None:
    workspace = FinalizationWorkspace(uuid4(), export_error=export_error)
    application = await make_application(
        tmp_path,
        workspace,
        make_artifact_store(tmp_path, fail=artifact_error),
    )

    result = await application.run(CancellationToken())

    assert result.run_result.status is RunStatus.SUCCEEDED
    assert result.finalization_errors[0].stage == expected_stage
    assert not workspace.destroyed


async def test_destroy_failure_is_reported_without_losing_result(
    tmp_path: Path,
) -> None:
    workspace = FinalizationWorkspace(uuid4(), destroy_error=True)
    application = await make_application(
        tmp_path,
        workspace,
        make_artifact_store(tmp_path),
    )

    result = await application.run(CancellationToken())

    assert result.run_result.status is RunStatus.SUCCEEDED
    assert result.artifacts
    assert result.finalization_errors[0].stage == "workspace_cleanup"


async def test_task_cancellation_propagates_and_preserves_workspace(
    tmp_path: Path,
) -> None:
    workspace = FinalizationWorkspace(uuid4(), export_error=True)
    application = await make_application(
        tmp_path,
        workspace,
        make_artifact_store(tmp_path),
        CancellingProvider(),
    )

    with pytest.raises(asyncio.CancelledError) as caught:
        await application.run(CancellationToken())

    assert caught.value.__notes__ == [
        "Finalization patch_export failed [PATCH_EXPORT_FAILED]: RuntimeError: export failed",
    ]
    assert not workspace.destroyed
