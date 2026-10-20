import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from miniagent.agents import AgentSpec
from miniagent.artifacts import ArtifactNotFoundError, ArtifactRef, ArtifactStore
from miniagent.cli.candidate_submission import CandidateSubmission, SubmissionResult
from miniagent.cli.diagnostic_finalization import finalize_diagnostic
from miniagent.cli.run_definition import parse_application_definition
from miniagent.cli.trajectory import (
    TrajectoryDisabled,
    TrajectoryExportOutcome,
    TrajectorySkipped,
    try_export_trajectory,
)
from miniagent.environments import Workspace
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    cleanup_deadline_scope,
)
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
from miniagent.runtime.cancellation import await_with_stop
from miniagent.runtime.interruption import InterruptionRequested, interruption_scope
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
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

    trajectory_export: TrajectoryExportOutcome = TrajectoryDisabled()
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
    export_trajectory: bool = False


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
        with (
            interruption_scope(interruption_token),
            cleanup_deadline_scope(
                lambda: interruption_token.deadline if interruption_token else None
            ),
        ):
            return await self._execute(
                cancellation_token,
                interruption_token=interruption_token,
                on_record_created=on_record_created,
            )

    async def resume(
        self,
        record: RunRecord,
        cancellation_token: CancellationToken,
        *,
        interruption_token: InterruptionToken | None = None,
    ) -> ApplicationResult:
        with (
            interruption_scope(interruption_token),
            cleanup_deadline_scope(
                lambda: interruption_token.deadline if interruption_token else None
            ),
        ):
            return await self._execute(
                cancellation_token, interruption_token=interruption_token, record=record
            )

    async def _execute(
        self,
        cancellation_token: CancellationToken,
        *,
        interruption_token: InterruptionToken | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        record: RunRecord | None = None,
    ) -> ApplicationResult:
        try:
            return await self._execute_run(
                cancellation_token,
                interruption_token=interruption_token,
                on_record_created=on_record_created,
                record=record,
            )
        except BaseException as exc:
            outcome = await self._export_trajectory(interruption_token)
            self._note_export(exc, outcome)
            raise

    async def _execute_run(
        self,
        cancellation_token: CancellationToken,
        *,
        interruption_token: InterruptionToken | None = None,
        on_record_created: Callable[[RunRecord], None] | None = None,
        record: RunRecord | None = None,
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
            artifact_store=dependencies.artifact_store,
            evaluator=dependencies.evaluator,
        )
        try:
            if record is None:
                run_result = await runtime.run(
                    dependencies.agent,
                    dependencies.task,
                    run_id=dependencies.run_id,
                    recovery_context=dependencies.recovery_context,
                    interruption_token=interruption_token,
                    on_record_created=on_record_created,
                )
            else:
                run_result = await runtime.resume(
                    record, interruption_token=interruption_token
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

        if interruption_token is not None and not interruption_token.is_requested:
            try:
                artifacts, errors = await await_with_stop(
                    self._finalize_patch(), CancellationToken(), interruption_token
                )
            except InterruptionRequested:
                artifacts, errors = await self._finalize_patch(
                    deadline=interruption_token.deadline, allow_export=False
                )
                errors.append(
                    FinalizationError(
                        stage="patch_export",
                        code="FINALIZATION_INTERRUPTED",
                        message="Finalization interrupted; original workspace retained",
                    )
                )
                outcome = await self._export_trajectory(interruption_token)
                return ApplicationResult(
                    trajectory_export=outcome,
                    run_result=run_result,
                    artifacts=tuple(artifacts),
                    finalization_errors=tuple(errors),
                )
        else:
            artifacts, errors = await self._finalize_patch(
                deadline=interruption_token.deadline if interruption_token else None
            )
        outcome = await self._export_trajectory(interruption_token)
        if isinstance(run_result, RunSuspension):
            return ApplicationResult(
                trajectory_export=outcome,
                run_result=run_result,
                artifacts=tuple(artifacts),
                finalization_errors=tuple(errors),
            )
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
            trajectory_export=outcome,
            run_result=run_result,
            artifacts=tuple(artifacts),
            finalization_errors=tuple(errors),
        )

    @staticmethod
    def _note_export(exc: BaseException, outcome: TrajectoryExportOutcome) -> None:
        if outcome.status == "exported":
            exc.add_note(f"Trajectory: {outcome.ref.uri}")
        elif outcome.status == "failed":
            exc.add_note(
                f"TRAJECTORY_EXPORT_FAILED [{outcome.code}]: {outcome.message}"
            )
        elif outcome.status == "skipped":
            exc.add_note(f"TRAJECTORY_EXPORT_SKIPPED: {outcome.reason}")

    async def _export_trajectory(
        self, token: InterruptionToken | None
    ) -> TrajectoryExportOutcome:
        deps = self.dependencies
        if not deps.export_trajectory:
            return TrajectoryDisabled()
        envelope = deps.recovery_context.application
        if envelope is None:
            return TrajectorySkipped(reason="Frozen application definition unavailable")
        try:
            record = await deps.run_store.get(deps.run_id)
            definition = parse_application_definition(envelope, record.state.definition)
        except Exception as exc:  # noqa: BLE001 -- Export failures cannot replace the Run outcome.
            return TrajectorySkipped(
                reason=f"Frozen boundary unavailable: {type(exc).__name__}: {exc}"
            )
        return await try_export_trajectory(
            enabled=True,
            run_id=deps.run_id,
            run_store=deps.run_store,
            artifact_store=deps.artifact_store,
            definition=definition,
            timeout_seconds=deps.artifact_collection_timeout_seconds,
            cleanup_deadline=token.deadline if token else None,
        )

    async def _finalize_patch(
        self,
        *,
        deadline: float | None = None,
        allow_export: bool = True,
    ) -> tuple[list[ArtifactRef], list[FinalizationError]]:
        errors: list[FinalizationError] = []
        submission = self.submission.result
        if not allow_export and submission.status == "not_attempted":
            submission = SubmissionResult()
        elif submission.status == "not_attempted":
            record = await self.dependencies.run_store.get(self.dependencies.run_id)
            submission = await finalize_diagnostic(
                recorder=RunRecorder.attach(self.dependencies.run_store, record),
                workspace=self.dependencies.workspace,
                artifact_store=self.dependencies.artifact_store,
                deadline=min(monotonic() + self.submission.timeout_seconds, deadline)
                if deadline is not None
                else monotonic() + self.submission.timeout_seconds,
                cleanup_timeout_seconds=self.submission.cleanup_timeout_seconds,
            )
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
        deadline = (
            min(
                monotonic() + self.dependencies.artifact_collection_timeout_seconds,
                deadline,
            )
            if deadline is not None
            else monotonic() + self.dependencies.artifact_collection_timeout_seconds
        )
        try:
            async with asyncio.timeout_at(deadline):
                refs: dict[UUID, ArtifactRef] = {}
                cursor = None
                while True:
                    page = await self.dependencies.artifact_store.list_records_for_run(
                        self.dependencies.run_id, cursor=cursor, limit=100
                    )
                    refs.update(
                        (record.artifact_id, record.reference())
                        for record in page.records
                    )
                    if page.next_cursor is None:
                        break
                    cursor = page.next_cursor
                if submission.patch_ref is not None:
                    await self.dependencies.artifact_store.get_record(
                        submission.patch_ref
                    )
            if (
                submission.patch_ref is not None
                and refs.get(submission.patch_ref.artifact_id) != submission.patch_ref
            ):
                raise ArtifactNotFoundError(
                    "Submitted patch is missing or changed in artifact collection: "
                    f"{submission.patch_ref.artifact_id}"
                )
            return list(refs.values()), errors
        except (RunPersistenceError, ManagedExecutionError, InterruptionRequested):
            raise
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
                except (
                    RunPersistenceError,
                    ManagedExecutionError,
                    InterruptionRequested,
                ):
                    raise
                except Exception as patch_error:  # noqa: BLE001
                    errors.append(
                        FinalizationError(
                            stage="artifact_collection",
                            code="PATCH_ARTIFACT_INVALID",
                            message=f"{type(patch_error).__name__}: {patch_error}",
                        )
                    )
            return [], errors
