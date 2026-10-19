import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from miniagent.agents import AgentSpec
from miniagent.artifacts import ArtifactNotFoundError, ArtifactRef, ArtifactStore
from miniagent.cli.candidate_submission import CandidateSubmission
from miniagent.environments import Workspace
from miniagent.environments.managed_execution import ManagedExecutionError
from miniagent.evaluation import Evaluator, PassThroughEvaluator
from miniagent.metering.pricing import PriceConfig
from miniagent.models.protocol import ModelProvider
from miniagent.policy import ApprovalHandler, PolicyContext
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    InterruptionToken,
    RunRecoveryContext,
    RunResult,
    RunStatus,
    RunSuspension,
    RuntimeConfig,
    RuntimeStore,
)
from miniagent.runtime.interruption import InterruptionRequested
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.store_contracts import RunRecord
from miniagent.tools.registry import ToolRegistry


class FinalizationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal[
        "patch_export",
        "artifact_registration",
        "artifact_collection",
        "workspace_cleanup",
    ]
    code: str
    message: str


class ApplicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_result: RunResult | RunSuspension
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
    config: RuntimeConfig = field(default_factory=RuntimeConfig)
    evaluator: Evaluator = field(default_factory=PassThroughEvaluator)
    candidate_submission: CandidateSubmission | None = None
    artifact_collection_timeout_seconds: float = 30.0


class Application:
    def __init__(self, dependencies: ApplicationDependencies) -> None:
        self.dependencies = dependencies
        self.submission = dependencies.candidate_submission or CandidateSubmission(
            dependencies.workspace, dependencies.artifact_store
        )

    async def run(
        self,
        cancellation_token: CancellationToken,
        *,
        interruption_token: InterruptionToken | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
    ) -> ApplicationResult:
        dependencies = self.dependencies
        runtime = AgentRuntime(
            provider=dependencies.provider,
            config=dependencies.config,
            tools=dependencies.tools,
            cancellation_token=cancellation_token,
            pricing=dependencies.pricing,
            policy_context=dependencies.policy_context,
            approval_handler=dependencies.approval_handler,
            run_store=dependencies.run_store,
            evaluator=dependencies.evaluator,
        )
        try:
            run_result = await runtime.run(
                dependencies.agent,
                dependencies.task,
                run_id=dependencies.run_id,
                recovery_context=dependencies.recovery_context,
                interruption_token=interruption_token,
                on_record_created=on_record_created,
            )
        except (RunPersistenceError, ManagedExecutionError, InterruptionRequested):
            raise
        except BaseException as exc:
            _, errors = await self._finalize_patch()
            for error in errors:
                exc.add_note(
                    f"Finalization {error.stage} failed [{error.code}]: {error.message}"
                )
            raise

        if isinstance(run_result, RunSuspension):
            return ApplicationResult(run_result=run_result)
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
        errors: list[FinalizationError] = []
        if self.submission.result.status == "not_attempted":
            await self.submission.submit(kind="diagnostic")
        submission = self.submission.result
        if submission.error is not None and submission.status != "empty":
            errors.append(
                FinalizationError(
                    stage="artifact_registration"
                    if submission.error.code == "ARTIFACT_REGISTRATION_FAILED"
                    else "patch_export",
                    code=submission.error.code,
                    message=submission.error.message,
                )
            )
        for diagnostic in submission.diagnostics:
            errors.append(
                FinalizationError(
                    stage="patch_export",
                    code="PATCH_EXPORT_CLEANUP_FAILED",
                    message=diagnostic,
                )
            )
        deadline = monotonic() + self.dependencies.artifact_collection_timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                records = await self.dependencies.artifact_store.list_for_run(
                    self.dependencies.run_id
                )
            refs = {record.artifact_id: record.reference() for record in records}
            if (
                submission.patch_ref is not None
                and refs.get(submission.patch_ref.artifact_id) != submission.patch_ref
            ):
                raise ArtifactNotFoundError(
                    "Submitted patch is missing or changed in artifact collection: "
                    f"{submission.patch_ref.artifact_id}"
                )
            return list(refs.values()), errors
        except Exception as exc:  # noqa: BLE001
            errors.append(
                FinalizationError(
                    stage="artifact_collection",
                    code="ARTIFACT_COLLECTION_FAILED",
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            if submission.patch_ref is not None and monotonic() < deadline:
                try:
                    async with asyncio.timeout_at(deadline):
                        record = await self.dependencies.artifact_store.get_record(
                            submission.patch_ref
                        )
                    return [record.reference()], errors
                except Exception as patch_error:  # noqa: BLE001
                    errors.append(
                        FinalizationError(
                            stage="artifact_collection",
                            code="PATCH_ARTIFACT_INVALID",
                            message=f"{type(patch_error).__name__}: {patch_error}",
                        )
                    )
            return [], errors
