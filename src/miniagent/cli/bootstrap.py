# File   : bootstrap.py
# Created: 2026-09-16 17:44:43
# Author : octy Sui

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from miniagent.agents import AgentSpec
from miniagent.artifacts import (
    ArtifactRef,
    ArtifactStore,
    ArtifactWrite,
    FileArtifactStore,
)
from miniagent.environments import (
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
    Workspace,
    WorkspaceKind,
)
from miniagent.models import PriceConfig, Settings, load_price_config
from miniagent.policy import ApprovalHandler, PolicyContext, digest_json
from miniagent.providers import ModelProvider
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import AgentRuntime, CancellationToken, RunResult
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


async def create_workspace(config: WorkspaceConfig) -> Workspace:
    artifact_store = FileArtifactStore(config.settings.artifact_root)
    if config.kind is WorkspaceKind.LOCAL:
        return await LocalWorkspaceBackend(
            root=config.settings.local_workspace_root,
            artifact_store=artifact_store,
        ).create(
            LocalWorkspaceSpec(
                run_id=config.run_id,
                source_dir=config.source_dir,
            )
        )

    image = config.docker_image or config.settings.docker_image
    if image is None:
        raise ValueError("Docker workspace requires --docker-image or DOCKER_IMAGE")
    return await DockerWorkspaceBackend(artifact_store=artifact_store).create(
        DockerWorkspaceSpec(run_id=config.run_id, image=image)
    )


@asynccontextmanager
async def workspace(config: WorkspaceConfig) -> AsyncIterator[Workspace]:
    instance = await create_workspace(config)
    try:
        yield instance
    finally:
        await instance.destroy()


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    agent: AgentSpec
    task: str
    source_dir: Path
    workspace_kind: WorkspaceKind | None = None
    docker_image: str | None = None
    approval_handler: ApprovalHandler | None = None


@dataclass(slots=True)
class BootstrappedAgent:
    agent: AgentSpec
    task: str
    run_id: UUID
    provider: ModelProvider
    tools: ToolRegistry
    pricing: PriceConfig
    policy_context: PolicyContext
    approval_handler: ApprovalHandler | None = None
    patch_artifact: ArtifactRef | None = None

    async def run(
        self,
        cancellation_token: CancellationToken,
    ) -> RunResult:
        runtime = AgentRuntime(
            provider=self.provider,
            tools=self.tools,
            cancellation_token=cancellation_token,
            pricing=self.pricing,
            policy_context=self.policy_context,
            approval_handler=self.approval_handler,
        )
        return await runtime.run(self.agent, self.task, run_id=self.run_id)


async def persist_workspace_patch(
    instance: Workspace,
    artifact_store: ArtifactStore,
) -> ArtifactRef | None:
    patch = await instance.export_patch()
    if not patch.content:
        return None

    metadata: dict[str, str | list[str]] = {"files": patch.files}
    if patch.base_revision is not None:
        metadata["base_revision"] = patch.base_revision
    return await artifact_store.put(
        ArtifactWrite(
            run_id=instance.run_id,
            type="workspace_patch",
            name="changes.patch",
            media_type="text/x-diff",
            metadata=metadata,
        ),
        patch.content.encode("utf-8"),
    )


@asynccontextmanager
async def bootstrap(config: BootstrapConfig) -> AsyncIterator[BootstrappedAgent]:
    settings = Settings()
    run_id = uuid4()
    workspace_config = WorkspaceConfig(
        kind=config.workspace_kind or WorkspaceKind(settings.workspace_backend),
        run_id=run_id,
        source_dir=config.source_dir,
        settings=settings,
        docker_image=config.docker_image,
    )
    async with workspace(workspace_config) as instance:
        bootstrapped_agent = BootstrappedAgent(
            agent=config.agent,
            task=config.task,
            run_id=run_id,
            provider=build_provider(config.agent, settings),
            tools=build_tools(instance),
            pricing=load_price_config(settings.model_pricing_file),
            policy_context=PolicyContext(
                workspace_id=instance.workspace_id,
                workspace_kind=workspace_config.kind.value,
                revision=digest_json(
                    {
                        "kind": workspace_config.kind.value,
                        "workspace_id": instance.workspace_id,
                        "docker_image": (
                            workspace_config.docker_image or settings.docker_image
                        ),
                    }
                ),
            ),
            approval_handler=config.approval_handler,
        )
        try:
            yield bootstrapped_agent
        except BaseException as exc:
            try:
                bootstrapped_agent.patch_artifact = await persist_workspace_patch(
                    instance,
                    FileArtifactStore(settings.artifact_root),
                )
            except Exception as patch_error:  # noqa: BLE001
                exc.add_note(f"Workspace patch export also failed: {patch_error}")
            raise
        else:
            bootstrapped_agent.patch_artifact = await persist_workspace_patch(
                instance,
                FileArtifactStore(settings.artifact_root),
            )
