import asyncio
from datetime import UTC, datetime
from importlib.metadata import version
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from miniagent.artifacts import ArtifactRef, ArtifactStore, ArtifactWrite
from miniagent.cli.run_definition import (
    CLIApplicationDefinition,
    SWEbenchDefinition,
    parse_application_definition,
)
from miniagent.runtime.recovery import ApplicationDefinitionEnvelope
from miniagent.runtime.store_contracts import RunRecord, RuntimeStore
from miniagent.trajectory import (
    TrajectoryDefinition,
    TrajectoryExportError,
    encode_trajectory,
)


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrajectoryDisabled(_Outcome):
    status: Literal["disabled"] = "disabled"


class TrajectoryExported(_Outcome):
    status: Literal["exported"] = "exported"
    ref: ArtifactRef


class TrajectoryFailed(_Outcome):
    status: Literal["failed"] = "failed"
    code: str
    message: str


class TrajectorySkipped(_Outcome):
    status: Literal["skipped"] = "skipped"
    reason: str


type TrajectoryExportOutcome = Annotated[
    TrajectoryDisabled | TrajectoryExported | TrajectoryFailed | TrajectorySkipped,
    Field(discriminator="status"),
]


def build_trajectory_definition(
    record: RunRecord, application: CLIApplicationDefinition
) -> TrajectoryDefinition:

    state = record.state
    context = record.recovery_context
    attachment = context.attachment if context else None
    benchmark = None
    if isinstance(application.evaluator, SWEbenchDefinition):
        benchmark = {
            "instance_id": application.evaluator.task_context.instance_id,
            "task_digest": application.evaluator.record_sha256,
            "manifest_sha256": application.evaluator.manifest_sha256,
            "tasks_sha256": application.evaluator.tasks_sha256,
            "harness_version": application.evaluator.harness_version,
        }
    return TrajectoryDefinition(
        task=state.identity.task,
        agent=state.definition.agent_spec,
        tools=state.definition.tool_definitions,
        budgets=state.definition.config,
        model_limits=application.model_limits.model_dump(mode="json"),
        pricing=application.pricing,
        provider_endpoint_identity=application.provider.endpoint_identity,
        workspace_kind=context.workspace_spec.kind if context else None,
        workspace_id=context.workspace_id if context else None,
        base_revision=attachment.base_revision if attachment else None,
        evaluator=application.evaluator.kind,
        benchmark=benchmark,
    )


async def try_export_trajectory(
    *,
    enabled: bool,
    run_id: UUID,
    run_store: RuntimeStore,
    artifact_store: ArtifactStore,
    definition: CLIApplicationDefinition | ApplicationDefinitionEnvelope,
    timeout_seconds: float,
    cleanup_deadline: float | None,
) -> TrajectoryExportOutcome:
    if not enabled:
        return TrajectoryDisabled()
    deadline = monotonic() + timeout_seconds
    if cleanup_deadline is not None:
        deadline = min(deadline, cleanup_deadline)
    if monotonic() >= deadline:
        return TrajectorySkipped(reason="Cleanup deadline exhausted")
    try:
        async with asyncio.timeout_at(deadline):
            record = await run_store.get(run_id)
            if isinstance(definition, ApplicationDefinitionEnvelope):
                definition = parse_application_definition(
                    definition, record.state.definition
                )
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- Read failure means no safe export boundary.
        return TrajectorySkipped(
            reason=f"Committed boundary unavailable: {type(exc).__name__}: {exc}"
        )
    try:
        async with asyncio.timeout_at(deadline):
            content = await encode_trajectory(
                record=record,
                definition=build_trajectory_definition(record, definition),
                store=run_store,
                artifacts=artifact_store,
                exported_at=datetime.now(UTC),
                producer_version=version("miniagent"),
                deadline=deadline,
            )
        if monotonic() >= deadline:
            return TrajectorySkipped(
                reason="Export deadline exhausted before publication"
            )
        ref = await artifact_store.put(
            ArtifactWrite(
                run_id=run_id,
                type="run_trajectory",
                name="trajectory.json",
                media_type="application/json",
                metadata={
                    "through_sequence": record.last_event_sequence,
                    "schema_version": 1,
                },
            ),
            content,
            deadline=deadline,
        )
        return TrajectoryExported(ref=ref)
    except asyncio.CancelledError:
        return TrajectorySkipped(reason="Export interrupted before publication")
    except TrajectoryExportError as exc:
        return TrajectoryFailed(code=exc.code, message=exc.message)
    except Exception as exc:  # noqa: BLE001 -- Export failures cannot replace the Run outcome.
        return TrajectoryFailed(
            code="TRAJECTORY_EXPORT_FAILED", message=f"{type(exc).__name__}: {exc}"
        )
