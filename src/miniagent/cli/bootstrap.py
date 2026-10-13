# File   : bootstrap.py
# Created: 2026-09-16 17:44:43
# Author : octy Sui

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.artifacts import (
    FileArtifactStore,
)
from miniagent.cli.application import (
    Application,
    ApplicationDependencies,
)
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
    WorkspaceCandidateEvaluator,
)
from miniagent.cli.model_limits import load_model_limits
from miniagent.cli.settings import Settings
from miniagent.environments import (
    DockerOptions,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
    LocalOptions,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
    Workspace,
    WorkspaceKind,
)
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.evaluation import PassThroughEvaluator
from miniagent.evaluation.swebench import SWEbenchEvaluator
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
    load_prepared_task,
)
from miniagent.evaluation.swebench_docker import (
    inspect_image,
    resolve_docker_connection,
    verify_harness_version,
)
from miniagent.metering.pricing import load_price_config
from miniagent.models.protocol import ModelProvider
from miniagent.policy import (
    ApprovalHandler,
    DefaultPolicyEngine,
    PolicyContext,
    digest_json,
)
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import (
    RunRecoveryContext,
    RuntimeConfig,
)
from miniagent.storage.sqlite import SQLiteRunStore
from miniagent.tools.read_file import ReadFileTool
from miniagent.tools.registry import ToolRegistry
from miniagent.tools.shell import ShellTool


def build_provider(agent: AgentSpec, settings: Settings) -> ModelProvider:
    if agent.model.provider == "qwen":
        return QwenProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    raise ValueError(f"Unknown provider: {agent.model.provider}")


def build_tools(workspace: Workspace) -> ToolRegistry:
    return ToolRegistry([ReadFileTool(workspace), ShellTool(workspace)])


@dataclass(frozen=True, slots=True)
class WorkspaceConfig:
    kind: WorkspaceKind
    run_id: UUID
    source_dir: Path
    settings: Settings
    docker_image: str | None = None


async def create_workspace(
    spec: LocalWorkspaceSpec | DockerWorkspaceSpec,
    settings: Settings,
) -> Workspace:
    artifact_store = FileArtifactStore(settings.artifact_root)
    if isinstance(spec, LocalWorkspaceSpec):
        return await LocalWorkspaceBackend(
            root=settings.local_workspace_root,
            artifact_store=artifact_store,
        ).create(spec)

    return await DockerWorkspaceBackend(artifact_store=artifact_store).create(spec)


def build_workspace_spec(
    config: WorkspaceConfig,
) -> LocalWorkspaceSpec | DockerWorkspaceSpec:
    if config.kind is WorkspaceKind.LOCAL:
        return LocalWorkspaceSpec(
            run_id=config.run_id,
            options=LocalOptions(source_dir=config.source_dir),
        )
    image = config.docker_image or config.settings.docker_image
    if image is None:
        raise ValueError("Docker workspace requires --docker-image or DOCKER_IMAGE")
    return DockerWorkspaceSpec(
        run_id=config.run_id,
        options=DockerOptions(image=image),
    )


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    agent: AgentSpec
    task: str
    source_dir: Path
    workspace_kind: WorkspaceKind | None = None
    docker_image: str | None = None
    approval_handler: ApprovalHandler | None = None
    swebench_task: SWEbenchTaskContext | None = None


async def bootstrap(config: BootstrapConfig) -> Application:
    settings = Settings()
    kind = config.workspace_kind or WorkspaceKind(settings.workspace_backend)
    prepared = None
    connection = None
    executable = None
    docker_executable = None
    environment = {}
    if kind is WorkspaceKind.DOCKER:
        if config.swebench_task is None:
            raise EvaluationPreparationError(
                "TASK_CONTEXT_REQUIRED",
                "Docker evaluation requires internal swebench_task context",
            )
        prepared = await asyncio.to_thread(
            load_prepared_task, settings.swebench_data_root, config.swebench_task
        )
        environment = dict(os.environ)
        harness_path = shutil.which(
            settings.swebench_executable, path=environment.get("PATH")
        )
        if harness_path is None:
            raise EvaluationPreparationError(
                "HARNESS_VERSION_MISMATCH", "SWE-bench executable is unavailable"
            )
        executable = Path(harness_path).absolute()
        await verify_harness_version(executable)
        docker_path = shutil.which(
            settings.evaluation_docker_executable, path=environment.get("PATH")
        )
        if docker_path is None:
            raise EvaluationPreparationError(
                "DOCKER_CONNECTION_INVALID", "Docker executable is unavailable"
            )
        docker_executable = Path(docker_path).absolute()
        connection = await resolve_docker_connection(docker_executable, environment)
        if (
            await inspect_image(docker_executable, connection, prepared.actual_image)
            is None
            and not settings.swebench_allow_image_pull
        ):
            raise EvaluationPreparationError(
                "EVALUATION_IMAGE_UNAVAILABLE", prepared.actual_image
            )
        try:
            await asyncio.to_thread(
                settings.swebench_work_root.expanduser().mkdir,
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise EvaluationPreparationError(
                "EVALUATION_DATA_INVALID", f"Evaluation work root is unavailable: {exc}"
            ) from exc
    limits = load_model_limits(
        settings.model_limits_file,
        config.agent.model.provider,
        config.agent.model.model,
    )
    runtime_config = RuntimeConfig(context_window_tokens=limits.context_window_tokens)
    runtime_config.validate_output_limit(limits.max_output_tokens)
    agent = config.agent.model_copy(
        update={
            "generation": config.agent.generation.model_copy(
                update={"max_output_tokens": limits.max_output_tokens}
            )
        }
    )
    run_id = uuid4()
    workspace_config = WorkspaceConfig(
        kind=kind,
        run_id=run_id,
        source_dir=config.source_dir,
        settings=settings,
        docker_image=config.docker_image,
    )
    workspace_spec = build_workspace_spec(workspace_config)
    instance = await create_workspace(workspace_spec, settings)
    try:
        artifact_store = FileArtifactStore(settings.artifact_root)
        submission = CandidateSubmission(
            instance,
            artifact_store,
            timeout_seconds=settings.candidate_submission_timeout_seconds,
            cleanup_timeout_seconds=settings.patch_export_cleanup_timeout_seconds,
        )
        evaluator = PassThroughEvaluator()
        if kind is WorkspaceKind.DOCKER:
            assert (
                prepared is not None
                and connection is not None
                and executable is not None
                and docker_executable is not None
            )
            evaluator = WorkspaceCandidateEvaluator(
                submission,
                SWEbenchEvaluator(
                    artifact_store=artifact_store,
                    prepared=prepared,
                    settings=settings,
                    model_name=agent.model.model,
                    executable=executable,
                    docker_executable=docker_executable,
                    connection=connection,
                    environment=environment,
                ),
            )
        run_store = SQLiteRunStore(settings.run_database_path)
        await run_store.initialize()
        return Application(
            ApplicationDependencies(
                agent=agent,
                config=runtime_config,
                task=config.task,
                run_id=run_id,
                provider=build_provider(agent, settings),
                tools=build_tools(instance),
                workspace=instance,
                evaluator=evaluator,
                candidate_submission=submission,
                artifact_collection_timeout_seconds=settings.artifact_collection_timeout_seconds,
                artifact_store=artifact_store,
                pricing=load_price_config(settings.model_pricing_file),
                policy_context=PolicyContext(
                    workspace_id=instance.workspace_id,
                    workspace_kind=workspace_config.kind.value,
                    revision=digest_json(
                        {
                            "kind": workspace_config.kind.value,
                            "workspace_id": instance.workspace_id,
                            "docker_image": (
                                workspace_spec.options.image
                                if isinstance(workspace_spec, DockerWorkspaceSpec)
                                else None
                            ),
                        }
                    ),
                ),
                approval_handler=config.approval_handler,
                run_store=run_store,
                recovery_context=RunRecoveryContext(
                    workspace_id=instance.workspace_id,
                    workspace_spec=persist_workspace_spec(workspace_spec),
                    policy_revision=DefaultPolicyEngine().revision,
                ),
            ),
        )
    except BaseException as exc:
        try:
            await instance.destroy()
        except Exception as cleanup_error:  # noqa: BLE001
            exc.add_note(f"Workspace cleanup also failed: {cleanup_error}")
        raise
