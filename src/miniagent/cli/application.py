from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from miniagent.agents import AgentSpec
from miniagent.artifacts import ArtifactRef, ArtifactStore, ArtifactWrite
from miniagent.environments import Workspace
from miniagent.metering.pricing import PriceConfig
from miniagent.models.protocol import ModelProvider
from miniagent.policy import ApprovalHandler, PolicyContext
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    RunRecoveryContext,
    RunResult,
    RunStatus,
    RuntimeStore,
)
from miniagent.tools.registry import ToolRegistry


class FinalizationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal["patch_export", "artifact_registration", "workspace_cleanup"]
    code: str
    message: str


class ApplicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_result: RunResult
    artifacts: tuple[ArtifactRef, ...] = ()
    finalization_errors: tuple[FinalizationError, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplicationDependencies:
    agent: AgentSpec
    task: str
    run_id: UUID
    provider: ModelProvider
    tools: ToolRegistry
    workspace: Workspace
    artifact_store: ArtifactStore
    pricing: PriceConfig
    policy_context: PolicyContext
    approval_handler: ApprovalHandler | None
    run_store: RuntimeStore
    recovery_context: RunRecoveryContext


class Application:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self.dependencies = dependencies

    async def run(self, cancellation_token: CancellationToken) -> ApplicationResult:
        dependencies = self.dependencies
        runtime = AgentRuntime(
            provider=dependencies.provider,
            tools=dependencies.tools,
            cancellation_token=cancellation_token,
            pricing=dependencies.pricing,
            policy_context=dependencies.policy_context,
            approval_handler=dependencies.approval_handler,
            run_store=dependencies.run_store,
        )
        try:
            run_result = await runtime.run(
                dependencies.agent,
                dependencies.task,
                run_id=dependencies.run_id,
                recovery_context=dependencies.recovery_context,
            )
        except BaseException as exc:
            _, errors = await self._finalize_patch()
            for error in errors:
                exc.add_note(
                    f"Finalization {error.stage} failed [{error.code}]: {error.message}"
                )
            raise

        artifacts, errors = await self._finalize_patch()
        if run_result.status is RunStatus.SUCCEEDED and not errors:
            try:
                await dependencies.workspace.destroy()
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    FinalizationError(
                        stage="workspace_cleanup",
                        code="WORKSPACE_DESTROY_FAILED",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
        return ApplicationResult(
            run_result=run_result,
            artifacts=tuple(artifacts),
            finalization_errors=tuple(errors),
        )

    async def _finalize_patch(
        self,
    ) -> tuple[list[ArtifactRef], list[FinalizationError]]:
        try:
            patch = await self.dependencies.workspace.export_patch()
        except Exception as exc:  # noqa: BLE001
            return [], [
                FinalizationError(
                    stage="patch_export",
                    code="PATCH_EXPORT_FAILED",
                    message=f"{type(exc).__name__}: {exc}",
                )
            ]
        if not patch.content:
            return [], []

        metadata: dict[str, str | list[str]] = {"files": patch.files}
        if patch.base_revision is not None:
            metadata["base_revision"] = patch.base_revision
        try:
            artifact = await self.dependencies.artifact_store.put(
                ArtifactWrite(
                    run_id=self.dependencies.workspace.run_id,
                    type="workspace_patch",
                    name="changes.patch",
                    media_type="text/x-diff",
                    metadata=metadata,
                ),
                patch.content.encode("utf-8"),
            )
        except Exception as exc:  # noqa: BLE001
            return [], [
                FinalizationError(
                    stage="artifact_registration",
                    code="ARTIFACT_REGISTRATION_FAILED",
                    message=f"{type(exc).__name__}: {exc}",
                )
            ]
        return [artifact], []
