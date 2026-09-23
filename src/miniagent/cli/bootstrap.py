# File   : bootstrap.py
# Created: 2026-09-16 17:44:43
# Author : octy Sui

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


async def bootstrap(config: BootstrapConfig) -> Application:
    settings = Settings()
    run_id = uuid4()
    workspace_config = WorkspaceConfig(
        kind=config.workspace_kind or WorkspaceKind(settings.workspace_backend),
        run_id=run_id,
        source_dir=config.source_dir,
        settings=settings,
        docker_image=config.docker_image,
    )
    workspace_spec = build_workspace_spec(workspace_config)
    instance = await create_workspace(workspace_spec, settings)
    try:
        artifact_store = FileArtifactStore(settings.artifact_root)
        run_store = SQLiteRunStore(settings.run_database_path)
        await run_store.initialize()
        return Application(
            ApplicationDependencies(
                agent=config.agent,
                task=config.task,
                run_id=run_id,
                provider=build_provider(config.agent, settings),
                tools=build_tools(instance),
                workspace=instance,
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
