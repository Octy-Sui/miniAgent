# File   : bootstrap.py
# Created: 2026-09-16 17:44:43
# Author : octy Sui

import asyncio
import os
import shlex
import shutil
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import typer

from miniagent.agents import AgentSpec
from miniagent.artifacts import (
    FileArtifactStore,
)
from miniagent.cli.application import (
    Application,
    ApplicationDependencies,
    ApplicationResult,
)
from miniagent.cli.candidate_submission import (
    CandidateSubmission,
    WorkspaceCandidateEvaluator,
)
from miniagent.cli.execution_scope import application_execution_scope
from miniagent.cli.model_limits import load_model_limits
from miniagent.cli.run_definition import (
    CLIApplicationDefinition,
    PassThroughDefinition,
    ProviderReference,
    SWEbenchDefinition,
    freeze_application_definition,
)
from miniagent.cli.settings import (
    ProviderCredentials,
    RunLocationSettings,
    Settings,
    load_provider_credentials,
)
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
from miniagent.environments.docker_connection import (
    DockerConnection,
    FrozenDockerConnection,
)
from miniagent.environments.managed_execution import current_cleanup_deadline
from miniagent.environments.persistence import (
    EnvironmentReference,
    persist_workspace_spec,
)
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
from miniagent.metering.pricing import PriceConfig, load_price_config
from miniagent.models.protocol import ModelProvider
from miniagent.policy import (
    ApprovalHandler,
    DefaultPolicyEngine,
    PolicyContext,
    digest_json,
)
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import (
    CancellationToken,
    InterruptionToken,
    RunRecoveryContext,
    RuntimeConfig,
    RuntimeStore,
)
from miniagent.runtime.cancellation import await_with_stop
from miniagent.runtime.interruption import InterruptionRequested
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.state import RunDefinition
from miniagent.runtime.store_contracts import (
    RunAlreadyExistsError,
    RunNotFoundError,
    RunRecord,
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
    *,
    connection: DockerConnection | None = None,
) -> Workspace:
    artifact_store = FileArtifactStore(settings.artifact_root)
    if isinstance(spec, LocalWorkspaceSpec):
        return await LocalWorkspaceBackend(
            root=settings.local_workspace_root,
            artifact_store=artifact_store,
        ).create(spec)

    return await DockerWorkspaceBackend(
        artifact_store=artifact_store, connection=connection
    ).create(spec)


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
    run_id: UUID | None = None


async def bootstrap(config: BootstrapConfig) -> Application:
    settings = Settings()
    kind = config.workspace_kind or WorkspaceKind(settings.workspace_backend)
    prepared = None
    connection = None
    workspace_connection = None
    image_content_id = None
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
        image_content_id = await inspect_image(
            docker_executable, connection, prepared.actual_image
        )
        if image_content_id is None:
            raise EvaluationPreparationError(
                "EVALUATION_IMAGE_UNAVAILABLE", prepared.actual_image
            )
        workspace_executable = shutil.which("docker", path=environment.get("PATH"))
        if workspace_executable is None:
            raise EvaluationPreparationError(
                "DOCKER_CONNECTION_INVALID",
                "Workspace Docker executable is unavailable",
            )
        workspace_connection = await resolve_docker_connection(
            Path(workspace_executable).absolute(), environment
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
    run_id = config.run_id or uuid4()
    workspace_config = WorkspaceConfig(
        kind=kind,
        run_id=run_id,
        source_dir=config.source_dir,
        settings=settings,
        docker_image=config.docker_image,
    )
    workspace_spec = build_workspace_spec(workspace_config)
    instance = (
        await create_workspace(
            workspace_spec, settings, connection=workspace_connection
        )
        if kind is WorkspaceKind.DOCKER
        else await create_workspace(workspace_spec, settings)
    )
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
                    prepared=replace(prepared, actual_image=image_content_id),
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
        tools = build_tools(instance)
        prices = load_price_config(settings.model_pricing_file)
        pricing = PriceConfig(
            currency=prices.currency,
            models=[prices.price_for(agent.model.provider, agent.model.model)],
        )
        definition = RunDefinition(
            agent_spec=agent,
            config=runtime_config,
            tool_definitions=tuple(tools.definitions(agent.allowed_tools)),
            tool_contract_metadata=tuple(
                tools.contract_metadata(name) for name in agent.allowed_tools
            ),
        )
        evaluator_definition = PassThroughDefinition()
        if prepared is not None:
            assert (
                config.swebench_task is not None
                and image_content_id is not None
                and executable is not None
                and docker_executable is not None
                and connection is not None
            )
            evaluator_definition = SWEbenchDefinition(
                task_context=config.swebench_task,
                record=prepared.record,
                record_sha256=prepared.record_sha256,
                manifest_sha256=prepared.manifest_sha256,
                tasks_sha256=prepared.tasks_sha256,
                harness_version="5.0.2",
                image_content_id=image_content_id,
                original_image_name=prepared.actual_image,
                data_root=settings.swebench_data_root.expanduser().resolve(),
                work_root=settings.swebench_work_root.expanduser().resolve(),
                harness_executable=executable,
                docker_executable=docker_executable,
                evaluation_connection=freeze_connection(connection),
                test_timeout_seconds=settings.swebench_test_timeout_seconds,
                process_timeout_seconds=settings.swebench_process_timeout_seconds,
                cleanup_timeout_seconds=settings.swebench_cleanup_timeout_seconds,
                artifact_timeout_seconds=settings.swebench_artifact_timeout_seconds,
                allow_image_pull=settings.swebench_allow_image_pull,
            )
        endpoint = urlsplit(settings.openai_base_url)
        host = endpoint.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if endpoint.port is not None:
            host += f":{endpoint.port}"
        application_definition = CLIApplicationDefinition(
            pricing=pricing,
            pricing_sha256=digest_json(pricing.model_dump(mode="json")),
            tool_contract_sha256=digest_json(
                {
                    "definitions": [
                        item.model_dump(mode="json")
                        for item in definition.tool_definitions
                    ],
                    "metadata": list(definition.tool_contract_metadata),
                }
            ),
            model_limits=limits,
            provider=ProviderReference(
                provider=agent.model.provider,
                model=agent.model.model,
                protocol_version="qwen-chat-completions-v1",
                endpoint_identity=urlunsplit(
                    (endpoint.scheme, host, endpoint.path, "", "")
                ),
                api_key_ref=EnvironmentReference(key="OPENAI_API_KEY"),
                endpoint_ref=EnvironmentReference(key="OPENAI_BASE_URL"),
            ),
            artifact_root=settings.artifact_root.expanduser().resolve(),
            model_limits_file=settings.model_limits_file.expanduser().resolve(),
            workspace_connection=freeze_connection(workspace_connection)
            if workspace_connection
            else None,
            evaluator=evaluator_definition,
            candidate_submission_timeout_seconds=settings.candidate_submission_timeout_seconds,
            patch_export_cleanup_timeout_seconds=settings.patch_export_cleanup_timeout_seconds,
            artifact_collection_timeout_seconds=settings.artifact_collection_timeout_seconds,
        )
        context = RunRecoveryContext(
            workspace_id=instance.workspace_id,
            workspace_spec=persist_workspace_spec(workspace_spec),
            attachment=instance.describe_attachment(),
            application=freeze_application_definition(
                application_definition, definition
            ),
            policy_revision=DefaultPolicyEngine().revision,
            pricing_revision=application_definition.pricing_sha256,
        )
        return Application(
            ApplicationDependencies(
                agent=agent,
                config=runtime_config,
                task=config.task,
                run_id=run_id,
                provider=build_provider(agent, settings),
                tools=tools,
                workspace=instance,
                evaluator=evaluator,
                candidate_submission=submission,
                artifact_collection_timeout_seconds=settings.artifact_collection_timeout_seconds,
                artifact_store=artifact_store,
                pricing=pricing,
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
                recovery_context=context,
            ),
        )
    except BaseException as exc:
        try:
            deadline = current_cleanup_deadline()
            async with asyncio.timeout_at(
                min(asyncio.get_running_loop().time() + 10, deadline)
                if deadline is not None
                else asyncio.get_running_loop().time() + 10
            ):
                await instance.destroy()
        except Exception as cleanup_error:  # noqa: BLE001
            exc.add_note(f"Workspace cleanup also failed: {cleanup_error}")
        raise


def freeze_connection(connection: DockerConnection) -> FrozenDockerConnection:
    return FrozenDockerConnection(
        source_context=connection.source_context,
        endpoint=connection.endpoint,
        cert_path=connection.cert_path,
        tls_verify=connection.tls_verify,
        daemon_id=connection.daemon_id,
    )


async def execute_new_run(
    config: BootstrapConfig,
    *,
    cancellation_token: CancellationToken,
    interruption_token: InterruptionToken | None = None,
) -> ApplicationResult:
    location = RunLocationSettings()
    run_id = config.run_id or uuid4()
    async with application_execution_scope(
        location.run_database_path, run_id, interruption_token=interruption_token
    ):
        if location.run_database_path.exists():
            try:
                await SQLiteRunStore(location.run_database_path, read_only=True).get(
                    run_id
                )
            except RunNotFoundError:
                pass
            else:
                raise RunAlreadyExistsError(run_id)
        application = await await_with_stop(
            bootstrap(replace(config, run_id=run_id)),
            cancellation_token,
            interruption_token,
        )

        def publish(record: RunRecord) -> None:
            typer.echo(f"Run: {record.state.identity.run_id}")
            typer.echo(
                f"Resume: miniagent resume --run-id {record.state.identity.run_id} --database {shlex.quote(str(location.run_database_path))}"
            )

        try:
            return await application.run(
                cancellation_token,
                interruption_token=interruption_token,
                on_record_created=publish,
            )
        except InterruptionRequested as exc:
            missing = not location.run_database_path.exists()
            if not missing:
                try:
                    await SQLiteRunStore(
                        location.run_database_path, read_only=True
                    ).get(run_id)
                except RunNotFoundError:
                    missing = True
            if missing:
                deadline = interruption_token.deadline if interruption_token else None
                try:
                    async with asyncio.timeout_at(deadline):
                        await application.dependencies.workspace.destroy()
                except Exception as cleanup_error:  # noqa: BLE001 -- preserve the controlling interruption.
                    exc.add_note(
                        f"Unpublished workspace cleanup failed: {type(cleanup_error).__name__}"
                    )
            raise
        except RunPersistenceError as exc:
            if exc.operation == "create" and isinstance(
                exc.__cause__, (sqlite3.Error, asyncio.CancelledError)
            ):
                store = SQLiteRunStore(location.run_database_path, read_only=True)
                try:
                    await store.get(run_id)
                except RunNotFoundError:
                    deadline = current_cleanup_deadline()
                    async with asyncio.timeout_at(
                        min(asyncio.get_running_loop().time() + 10, deadline)
                        if deadline is not None
                        else asyncio.get_running_loop().time() + 10
                    ):
                        await application.dependencies.workspace.destroy()
            raise


class ApplicationRehydrationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def rehydrate_application(
    record: RunRecord,
    workspace: Workspace,
    definition: CLIApplicationDefinition,
    *,
    run_store: RuntimeStore,
    approval_handler: ApprovalHandler | None = None,
    credentials: ProviderCredentials | None = None,
) -> Application:
    from miniagent.evaluation.swebench_data import PreparedTask

    context = record.recovery_context
    assert context is not None
    provider = definition.provider
    if provider.protocol_version != "qwen-chat-completions-v1":
        raise ApplicationRehydrationError(
            "MODEL_CONFIG_MISMATCH", "Provider protocol differs"
        )
    credentials = credentials or load_provider_credentials(
        provider.api_key_ref.key, provider.endpoint_ref.key
    )
    api_key = credentials.api_key
    endpoint = credentials.endpoint
    if not api_key or not endpoint:
        raise ApplicationRehydrationError(
            "ENVIRONMENT_REFERENCE_MISSING",
            "Provider credentials or endpoint unavailable",
        )
    values: dict[str, object] = {
        "openai_api_key": api_key,
        "openai_base_url": endpoint,
        "artifact_root": definition.artifact_root,
        "model_limits_file": definition.model_limits_file,
        "candidate_submission_timeout_seconds": definition.candidate_submission_timeout_seconds,
        "patch_export_cleanup_timeout_seconds": definition.patch_export_cleanup_timeout_seconds,
        "artifact_collection_timeout_seconds": definition.artifact_collection_timeout_seconds,
    }
    frozen = definition.evaluator
    if isinstance(frozen, SWEbenchDefinition):
        values.update(
            swebench_data_root=frozen.data_root,
            swebench_work_root=frozen.work_root,
            swebench_executable=str(frozen.harness_executable),
            evaluation_docker_executable=str(frozen.docker_executable),
            swebench_test_timeout_seconds=frozen.test_timeout_seconds,
            swebench_process_timeout_seconds=frozen.process_timeout_seconds,
            swebench_cleanup_timeout_seconds=frozen.cleanup_timeout_seconds,
            swebench_artifact_timeout_seconds=frozen.artifact_timeout_seconds,
            swebench_allow_image_pull=False,
        )
    settings = Settings.model_construct(_fields_set=set(values), **values)
    artifacts = FileArtifactStore(definition.artifact_root)
    tools = build_tools(workspace)
    state = record.state
    if (
        tuple(tools.definitions(state.definition.agent_spec.allowed_tools))
        != state.definition.tool_definitions
        or tuple(
            tools.contract_metadata(name)
            for name in state.definition.agent_spec.allowed_tools
        )
        != state.definition.tool_contract_metadata
    ):
        raise ApplicationRehydrationError(
            "TOOL_CONTRACT_MISMATCH", "Current tool contract differs"
        )
    submission = CandidateSubmission(
        workspace,
        artifacts,
        timeout_seconds=definition.candidate_submission_timeout_seconds,
        cleanup_timeout_seconds=definition.patch_export_cleanup_timeout_seconds,
    )
    evaluator = PassThroughEvaluator()
    if isinstance(frozen, SWEbenchDefinition):
        from miniagent.environments.docker_connection import bind_docker_connection

        evaluator = WorkspaceCandidateEvaluator(
            submission,
            SWEbenchEvaluator(
                artifact_store=artifacts,
                prepared=PreparedTask(
                    record=frozen.record,
                    manifest_sha256=frozen.manifest_sha256,
                    tasks_sha256=frozen.tasks_sha256,
                    record_sha256=frozen.record_sha256,
                    harness_version=frozen.harness_version,
                    default_image=frozen.original_image_name,
                    actual_image=frozen.image_content_id,
                    image_preflight_verified=True,
                ),
                settings=settings,
                model_name=state.definition.agent_spec.model.model,
                executable=frozen.harness_executable,
                docker_executable=frozen.docker_executable,
                connection=bind_docker_connection(
                    frozen.evaluation_connection, dict(os.environ)
                ),
                environment=dict(os.environ),
            ),
        )
    return Application(
        ApplicationDependencies(
            agent=state.definition.agent_spec,
            task=state.identity.task,
            run_id=state.identity.run_id,
            provider=build_provider(state.definition.agent_spec, settings),
            tools=tools,
            workspace=workspace,
            artifact_store=artifacts,
            pricing=definition.pricing,
            policy_context=PolicyContext(
                workspace_id=workspace.workspace_id,
                workspace_kind=context.workspace_spec.kind.value,
                revision=digest_json(
                    {
                        "kind": context.workspace_spec.kind.value,
                        "workspace_id": workspace.workspace_id,
                        "docker_image": context.workspace_spec.options.image
                        if isinstance(context.workspace_spec.options, DockerOptions)
                        else None,
                    }
                ),
            ),
            approval_handler=approval_handler,
            run_store=run_store,
            recovery_context=context,
            config=state.definition.config,
            evaluator=evaluator,
            candidate_submission=submission,
            artifact_collection_timeout_seconds=definition.artifact_collection_timeout_seconds,
        )
    )
