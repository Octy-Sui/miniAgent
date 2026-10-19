import asyncio
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from miniagent.artifacts import FileArtifactStore
from miniagent.cli.application import ApplicationResult
from miniagent.cli.bootstrap import build_tools, rehydrate_application
from miniagent.cli.candidate_submission import (
    CandidateArtifactInvalid,
    validate_candidate_artifact,
)
from miniagent.cli.console_approval import ConsoleApprovalHandler
from miniagent.cli.execution_scope import application_execution_scope
from miniagent.cli.model_limits import load_model_limits
from miniagent.cli.run_definition import (
    CandidateSubmissionState,
    CLIApplicationDefinition,
    SWEbenchDefinition,
    parse_application_definition,
)
from miniagent.environments import (
    DockerWorkspace,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
    LocalWorkspace,
    LocalWorkspaceBackend,
    LocalWorkspaceSpec,
)
from miniagent.environments.attachment import (
    AttachmentFacts,
    DockerAttachment,
    LocalAttachment,
)
from miniagent.environments.config import DockerOptions, LocalOptions
from miniagent.environments.docker_connection import bind_docker_connection
from miniagent.environments.docker_exec import (
    inspect_docker_execution,
    inspect_guest_execution,
)
from miniagent.environments.managed_execution import (
    DockerExecIdentity,
    HostProcessIdentity,
)
from miniagent.environments.process import inspect_host_execution
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    load_prepared_task,
)
from miniagent.evaluation.swebench_docker import (
    EvaluationAttemptFacts,
    SWEbenchAttemptState,
    check_daemon,
    cleanup_attempt_resources,
    inspect_attempt_resources,
    inspect_image,
    verify_harness_version,
)
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime import CancellationToken, InterruptionToken
from miniagent.runtime.cancellation import await_with_stop
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalResolved,
    DiagnosticFinalizationExecutionEnded,
    DiagnosticFinalizationInterrupted,
    NewRunEvent,
    ProviderIdentityChanged,
    ResumeCompleted,
    ResumeFailed,
    ResumeStarted,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.interruption import InterruptionRequested, raise_if_interrupted
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.resume import (
    ResumeEnvironmentFacts,
    ResumeHistoryFacts,
    ResumePlan,
    ResumeRejected,
    ResumeRejection,
    history_facts,
    plan_resume,
)
from miniagent.runtime.state import TERMINAL_STATUSES
from miniagent.runtime.store_contracts import (
    CorruptStoredDataError,
    RunNotFoundError,
    RunRecord,
    UnsupportedStorageVersionError,
)
from miniagent.runtime.transitions import begin_resume, complete_resume
from miniagent.storage import SQLiteRunStore


@dataclass(frozen=True, slots=True)
class ResumeConfig:
    run_id: UUID
    database: Path
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class _Inspection:
    record: RunRecord
    definition: CLIApplicationDefinition
    history: ResumeHistoryFacts
    environment: ResumeEnvironmentFacts
    backend: LocalWorkspaceBackend | DockerWorkspaceBackend
    evaluation: EvaluationAttemptFacts | None
    plan: ResumePlan | ResumeRejection


def _rejection(config: ResumeConfig, code: str, message: str) -> ResumeRejection:
    return ResumeRejection(code=code, run_id=config.run_id, message=message)


async def _inspect(config: ResumeConfig) -> _Inspection | ResumeRejection:
    store = SQLiteRunStore(config.database, read_only=True)
    if not store.path.exists():
        return _rejection(config, "RUN_NOT_FOUND", "Run database does not exist")
    try:
        record = await store.get(config.run_id)
        events = tuple(await store.list_events(config.run_id))
        approvals = tuple(await store.list_unconsumed_approvals(config.run_id))
        step = record.state.execution.current_step
        results = (
            tuple(
                await store.list_tool_results(
                    config.run_id, step_number=step.step_number
                )
            )
            if step and record.state.execution.tool_batch is not None
            else ()
        )
    except RunNotFoundError:
        return _rejection(config, "RUN_NOT_FOUND", "Run does not exist")
    except UnsupportedStorageVersionError:
        return _rejection(
            config, "RESUME_SCHEMA_UNSUPPORTED", "Storage schema is unsupported"
        )
    except (CorruptStoredDataError, ValueError):
        return _rejection(
            config, "RESUME_STATE_INCONSISTENT", "Stored state or history is invalid"
        )
    if record.state.lifecycle.status in TERMINAL_STATUSES:
        return _rejection(
            config,
            "RUN_NOT_RESUMABLE",
            "Terminal runs cannot resume business execution",
        )
    context = record.recovery_context
    if context is None or context.attachment is None or context.application is None:
        return _rejection(
            config,
            "RESUME_METADATA_MISSING",
            "Retained attachment and frozen definition are required",
        )
    try:
        definition = parse_application_definition(
            context.application, record.state.definition
        )
    except ValueError:
        return _rejection(
            config,
            "RESUME_STATE_INCONSISTENT",
            "Frozen application definition is invalid",
        )
    history = history_facts(record, events, tool_results=results, approvals=approvals)
    artifacts = FileArtifactStore(definition.artifact_root)
    attachment = context.attachment
    backend: LocalWorkspaceBackend | DockerWorkspaceBackend
    if isinstance(attachment, LocalAttachment):
        backend = LocalWorkspaceBackend(root=attachment.root, artifact_store=artifacts)
    else:
        backend = DockerWorkspaceBackend(artifact_store=artifacts)
    # Apply the pure early gates before any external inspection.
    provisional = ResumeEnvironmentFacts(
        AttachmentFacts(
            run_id=config.run_id,
            workspace_id=context.workspace_id,
            exists=True,
            identity_matches=True,
        )
    )
    early = plan_resume(record, history, provisional)
    if isinstance(early, ResumeRejection) and early.code in (
        "RUN_NOT_RESUMABLE",
        "RESUME_METADATA_MISSING",
        "RESUME_STATE_INCONSISTENT",
        "UNCERTAIN_TOOL_EXECUTION",
    ):
        return early
    if isinstance(backend, LocalWorkspaceBackend):
        assert isinstance(attachment, LocalAttachment)
        facts = await backend.inspect_attachment(attachment)
    else:
        assert isinstance(attachment, DockerAttachment)
        facts = await backend.inspect_attachment(attachment)
    evidence = []
    for chain in history.execution_chains:
        if chain.attempt_id in history.interrupted_attempt_ids:
            continue
        persisted = {item.execution_id: item for item in chain.evidence}
        for identity in chain.identities:
            if identity.execution_id in persisted:
                evidence.append(persisted[identity.execution_id])
            elif isinstance(identity, HostProcessIdentity):
                evidence.append(inspect_host_execution(identity))
            elif (
                isinstance(attachment, DockerAttachment)
                and facts.container_status == "running"
            ):
                evidence.append(
                    await inspect_docker_execution(
                        identity,
                        connection=bind_docker_connection(
                            attachment.connection, dict(os.environ)
                        ),
                    )
                )
    environment = ResumeEnvironmentFacts(facts, execution_evidence=tuple(evidence))
    early = plan_resume(record, history, environment)
    evaluation_facts = None
    if isinstance(early, ResumePlan):
        dependencies = []
        if isinstance(attachment, LocalAttachment):
            assert isinstance(backend, LocalWorkspaceBackend)
            assert isinstance(context.workspace_spec.options, LocalOptions)
            reference = LocalWorkspace(
                backend=backend,
                spec=LocalWorkspaceSpec(
                    run_id=config.run_id, options=context.workspace_spec.options
                ),
                _workspace_id=attachment.workspace_id,
                root=attachment.repository_root,
                base_revision=attachment.base_revision,
            )
        else:
            assert isinstance(backend, DockerWorkspaceBackend)
            assert isinstance(context.workspace_spec.options, DockerOptions)
            reference = DockerWorkspace(
                backend=backend,
                spec=DockerWorkspaceSpec(
                    run_id=config.run_id, options=context.workspace_spec.options
                ),
                _workspace_id=attachment.workspace_id,
                container_id=attachment.container_id,
                image_id=attachment.image_id,
                base_revision=attachment.base_revision,
            )
        tools = build_tools(reference)
        allowed = record.state.definition.agent_spec.allowed_tools
        try:
            if (
                tuple(tools.definitions(allowed))
                != record.state.definition.tool_definitions
                or tuple(tools.contract_metadata(name) for name in allowed)
                != record.state.definition.tool_contract_metadata
            ):
                dependencies.append(
                    _rejection(
                        config,
                        "TOOL_CONTRACT_MISMATCH",
                        "Current tool contract differs from the frozen definition",
                    )
                )
        except KeyError:
            dependencies.append(
                _rejection(
                    config, "TOOL_CONTRACT_MISMATCH", "Original tool is unavailable"
                )
            )
        if (
            definition.provider.provider != "qwen"
            or definition.provider.protocol_version != "qwen-chat-completions-v1"
        ):
            dependencies.append(
                _rejection(
                    config,
                    "MODEL_CONFIG_MISMATCH",
                    "Original provider protocol is unavailable",
                )
            )
        try:
            current_limits = await asyncio.to_thread(
                load_model_limits,
                definition.model_limits_file,
                record.state.definition.agent_spec.model.provider,
                record.state.definition.agent_spec.model.model,
            )
            original = record.state.definition
            if (
                current_limits.context_window_tokens
                < original.config.context_window_tokens
                or (
                    original.agent_spec.generation.max_output_tokens is not None
                    and current_limits.max_output_tokens
                    < original.agent_spec.generation.max_output_tokens
                )
            ):
                raise ValueError("Current capacity cannot support the frozen request")
        except (OSError, ValueError):
            dependencies.append(
                _rejection(
                    config,
                    "MODEL_CONFIG_MISMATCH",
                    "Current model capacity data is missing or incompatible with the frozen request",
                )
            )
        candidate_rejection = None
        evaluation_rejection = None
        frozen = definition.evaluator
        submission = record.state.execution.evaluation.submission
        candidate = None
        if submission is not None:
            try:
                if (
                    submission.kind != "candidate_submission"
                    or submission.schema_version != 1
                ):
                    raise ValueError("Candidate schema differs")
                candidate = CandidateSubmissionState.model_validate(submission.payload)
                text = record.state.lifecycle.candidate_output
                if (
                    text is None
                    or sha256(text.encode()).hexdigest() != candidate.candidate_sha256
                ):
                    raise ValueError("Candidate text differs")
                if candidate.status == "registered":
                    await validate_candidate_artifact(
                        candidate, artifacts, config.run_id
                    )
            except (CandidateArtifactInvalid, ValueError):
                candidate_rejection = _rejection(
                    config,
                    "CANDIDATE_ARTIFACT_INVALID",
                    "Committed candidate artifact or binding is invalid",
                )
        if isinstance(frozen, SWEbenchDefinition):
            try:
                connection = bind_docker_connection(
                    frozen.evaluation_connection, dict(os.environ)
                )
                await check_daemon(frozen.docker_executable, connection)
                await verify_harness_version(frozen.harness_executable)
                prepared = load_prepared_task(frozen.data_root, frozen.task_context)
                if (
                    prepared.record_sha256,
                    prepared.manifest_sha256,
                    prepared.tasks_sha256,
                ) != (
                    frozen.record_sha256,
                    frozen.manifest_sha256,
                    frozen.tasks_sha256,
                ):
                    raise EvaluationPreparationError(
                        "EVALUATION_CONTEXT_MISMATCH", "Frozen evaluation data differs"
                    )
                if (
                    await inspect_image(
                        frozen.docker_executable, connection, frozen.image_content_id
                    )
                    != frozen.image_content_id
                ):
                    raise EvaluationPreparationError(
                        "EVALUATION_IMAGE_UNAVAILABLE",
                        "Frozen image content is unavailable",
                    )
                attempt = record.state.execution.evaluation.attempt
                if (
                    attempt is not None
                    and record.state.execution.evaluation.result is None
                ):
                    if (
                        attempt.kind != "swebench_attempt"
                        or attempt.schema_version != 1
                    ):
                        raise ValueError("Attempt schema differs")
                    old = SWEbenchAttemptState.model_validate(attempt.payload)
                    if candidate is None or (
                        old.patch_ref,
                        old.patch_sha256,
                        old.task_digest,
                    ) != (
                        candidate.patch_ref,
                        candidate.patch_sha256,
                        frozen.record_sha256,
                    ):
                        raise ValueError("Attempt candidate binding differs")
                    chain = next(
                        (
                            item
                            for item in history.execution_chains
                            if item.attempt_id == old.attempt_id
                        ),
                        None,
                    )
                    evaluation_facts = await inspect_attempt_resources(
                        old,
                        connection,
                        docker_executable=frozen.docker_executable,
                        host_executions=chain.identities if chain else (),
                    )
                    if not evaluation_facts.host_ended or evaluation_facts.errors:
                        evaluation_rejection = _rejection(
                            config,
                            "EVALUATION_ATTEMPT_BUSY",
                            "Old evaluation resources are not safely inspectable",
                        )
            except EvaluationPreparationError as exc:
                dependencies.append(
                    _rejection(
                        config,
                        exc.code,
                        "Frozen evaluation dependency is unavailable or differs",
                    )
                )
            except (ValueError, OSError):
                dependencies.append(
                    _rejection(
                        config,
                        "EVALUATION_CONTEXT_MISMATCH",
                        "Frozen evaluation configuration is unavailable or differs",
                    )
                )
        for reference in context.workspace_spec.environment_refs.values():
            if reference.key not in os.environ:
                dependencies.append(
                    _rejection(
                        config,
                        "ENVIRONMENT_REFERENCE_MISSING",
                        "Workspace environment reference is unavailable",
                    )
                )
        environment = replace(
            environment,
            dependency_rejections=tuple(dependencies),
            candidate_rejection=candidate_rejection,
            evaluation_rejection=evaluation_rejection,
        )
    return _Inspection(
        record,
        definition,
        history,
        environment,
        backend,
        evaluation_facts,
        plan_resume(record, history, environment),
    )


async def inspect_resume(config: ResumeConfig) -> ResumePlan | ResumeRejection:
    inspection = await _inspect(config)
    return inspection if isinstance(inspection, ResumeRejection) else inspection.plan


async def resume_application(
    config: ResumeConfig,
    *,
    cancellation_token: CancellationToken,
    interruption_token: InterruptionToken | None = None,
) -> ApplicationResult | ResumePlan:
    async with application_execution_scope(
        config.database, config.run_id, interruption_token=interruption_token
    ):
        inspected = await await_with_stop(
            _inspect(config), cancellation_token, interruption_token
        )
        plan = inspected if isinstance(inspected, ResumeRejection) else inspected.plan
        if isinstance(plan, ResumeRejection):
            raise ResumeRejected(plan)
        if config.dry_run:
            return plan
        assert isinstance(inspected, _Inspection)
        store = SQLiteRunStore(config.database)
        record = await store.get(config.run_id)
        if record != inspected.record:
            raise ResumeRejected(
                _rejection(
                    config,
                    "RESUME_STATE_INCONSISTENT",
                    "Source record changed after inspection",
                )
            )
        journal = RunJournal.attach(
            RunRecorder.attach(store, record), inspected.history.approvals
        )
        if interruption_token is not None and interruption_token.is_requested:
            raise InterruptionRequested
        state = begin_resume(
            record.state, attempt_id=uuid4(), source_sequence=record.last_event_sequence
        )
        session = state.recovery.session
        assert session is not None
        pending = record.state.approval.pending
        resolution = None
        started = [
            NewRunEvent(
                run_id=config.run_id,
                payload=ResumeStarted(
                    resume_attempt_id=session.attempt_id,
                    source_sequence=session.source_sequence,
                    business_status=session.business_status,
                    phase=session.phase_at_start,
                ),
            )
        ]
        if pending is not None:
            stored = next(
                item
                for item in inspected.history.approvals
                if item.approval.request.approval_id == pending.approval_id
            )
            if stored.approval.resolution is None:
                resolution = ApprovalResolution(
                    approval_id=pending.approval_id,
                    outcome=ApprovalOutcome.INVALIDATED,
                    actor_kind="system",
                    actor_id="resume",
                    reason="Original pending authorization invalidated by resume",
                    resolved_at=datetime.now(UTC),
                )
                started.append(
                    NewRunEvent(
                        run_id=config.run_id,
                        payload=ApprovalResolved(
                            approval_id=pending.approval_id,
                            outcome="invalidated",
                            call_index=pending.call_index,
                            tool_call_id=pending.tool_call_id,
                            tool_name=stored.approval.decision.request.tool_name,
                        ),
                    )
                )
        committed = await journal.start_resume(
            state, started, approval_resolution=resolution
        )
        execution = RunExecution.attach(committed.record, journal)

        async def prepare():
            try:
                context = record.recovery_context
                assert context is not None and context.attachment is not None
                values = {
                    key: os.environ[reference.key]
                    for key, reference in context.workspace_spec.environment_refs.items()
                }
                if isinstance(context.attachment, LocalAttachment):
                    assert isinstance(context.workspace_spec.options, LocalOptions)
                    assert isinstance(inspected.backend, LocalWorkspaceBackend)
                    spec = LocalWorkspaceSpec(
                        run_id=config.run_id,
                        options=context.workspace_spec.options,
                        environment_values=values,
                    )
                    workspace = await inspected.backend.attach(spec, context.attachment)
                else:
                    assert isinstance(context.workspace_spec.options, DockerOptions)
                    assert isinstance(inspected.backend, DockerWorkspaceBackend)
                    spec = DockerWorkspaceSpec(
                        run_id=config.run_id,
                        options=context.workspace_spec.options,
                        environment_values=values,
                    )
                    workspace = await inspected.backend.attach(
                        spec, context.attachment, before_start=raise_if_interrupted
                    )
                live_evidence = list(inspected.environment.execution_evidence)
                for chain in inspected.history.execution_chains:
                    if chain.attempt_id in inspected.history.interrupted_attempt_ids:
                        continue
                    for identity in chain.identities:
                        if isinstance(identity, DockerExecIdentity) and not any(
                            item.identity == identity
                            and item.outcome == "ended"
                            and item.group_ended
                            for item in live_evidence
                        ):
                            evidence = await inspect_guest_execution(
                                identity, workspace
                            )
                            if evidence.outcome != "ended" or not evidence.group_ended:
                                raise ResumeRejected(
                                    _rejection(
                                        config,
                                        "WORKSPACE_BUSY",
                                        "Original guest group is not confirmed ended",
                                    )
                                )
                            live_evidence.append(evidence)
                for evidence in live_evidence:
                    if (
                        evidence.outcome == "ended"
                        and evidence.group_ended
                        and evidence.issuer_ended
                    ):
                        operation = execution.state.execution.active_operation
                        if (
                            operation is not None
                            and evidence.identity.attempt_id == operation.attempt_id
                        ):
                            await execution.execution_observer(
                                operation.attempt_id
                            ).ended(evidence)
                for chain in inspected.history.execution_chains:
                    if (
                        chain.scope != "diagnostic"
                        or chain.attempt_id in inspected.history.interrupted_attempt_ids
                    ):
                        continue
                    proof = {
                        item.execution_id: item
                        for item in (*chain.evidence, *live_evidence)
                    }
                    closed = []
                    for identity in chain.identities:
                        evidence = proof.get(identity.execution_id)
                        if (
                            evidence is None
                            or evidence.identity != identity
                            or evidence.outcome != "ended"
                            or not evidence.group_ended
                            or not evidence.issuer_ended
                        ):
                            raise ResumeRejected(
                                _rejection(
                                    config,
                                    "WORKSPACE_BUSY",
                                    "Old diagnostic execution end is unconfirmed",
                                )
                            )
                        if evidence not in chain.evidence:
                            closed.append(
                                NewRunEvent(
                                    run_id=config.run_id,
                                    payload=DiagnosticFinalizationExecutionEnded(
                                        attempt_id=chain.attempt_id, evidence=evidence
                                    ),
                                )
                            )
                    closed.append(
                        NewRunEvent(
                            run_id=config.run_id,
                            payload=DiagnosticFinalizationInterrupted(
                                attempt_id=chain.attempt_id,
                                reason="Verified old diagnostic chain closed for resume",
                                execution_ended=True,
                            ),
                        )
                    )
                    await journal.record_activity(execution.state, closed)
                if inspected.evaluation is not None:
                    assert isinstance(
                        inspected.definition.evaluator, SWEbenchDefinition
                    )
                    await cleanup_attempt_resources(
                        inspected.evaluation,
                        deadline=monotonic()
                        + inspected.definition.evaluator.cleanup_timeout_seconds,
                    )
                assert journal.recorder.record is not None
                application = rehydrate_application(
                    journal.recorder.record,
                    workspace,
                    inspected.definition,
                    run_store=store,
                    approval_handler=ConsoleApprovalHandler(),
                )
                endpoint = urlsplit(
                    os.environ[inspected.definition.provider.endpoint_ref.key]
                )
                host = endpoint.hostname or ""
                if ":" in host:
                    host = f"[{host}]"
                if endpoint.port is not None:
                    host += f":{endpoint.port}"
                endpoint_identity = urlunsplit(
                    (endpoint.scheme, host, endpoint.path, "", "")
                )
                original_endpoint = inspected.definition.provider.endpoint_identity
                if endpoint_identity != original_endpoint:
                    await journal.record_activity(
                        execution.state,
                        [
                            NewRunEvent(
                                run_id=config.run_id,
                                payload=ProviderIdentityChanged(
                                    original_endpoint_identity=original_endpoint,
                                    current_endpoint_identity=endpoint_identity,
                                ),
                            )
                        ],
                    )
                for stored in inspected.history.approvals:
                    approval = stored.approval
                    if (
                        approval.resolution is not None
                        and approval.resolution.outcome is ApprovalOutcome.APPROVED
                        and stored.authorization_invalidated_event_id is None
                    ):
                        await journal.invalidate_approval(
                            execution.state,
                            [
                                NewRunEvent(
                                    run_id=config.run_id,
                                    payload=ApprovalInvalidated(
                                        approval_id=approval.request.approval_id,
                                        reason="Resume requires current policy and fresh authorization",
                                    ),
                                )
                            ],
                            approval.request.approval_id,
                        )
                if plan.close_operations:
                    operation = execution.state.execution.active_operation
                    assert operation is not None
                    await execution.interrupt_operation(
                        unconfirmed=operation.kind in ("model", "summary")
                    )
                completed = await journal.complete_resume(
                    complete_resume(execution.state),
                    [
                        NewRunEvent(
                            run_id=config.run_id,
                            payload=ResumeCompleted(
                                resume_attempt_id=session.attempt_id,
                                source_sequence=session.source_sequence,
                                business_status=session.business_status,
                                phase=session.phase_at_start,
                            ),
                        )
                    ],
                )
            except (RunPersistenceError, InterruptionRequested):
                raise
            except Exception as exc:
                code = getattr(exc, "code", "WORKSPACE_UNAVAILABLE")
                await journal.record_activity(
                    execution.state,
                    [
                        NewRunEvent(
                            run_id=config.run_id,
                            payload=ResumeFailed(
                                resume_attempt_id=session.attempt_id,
                                source_sequence=session.source_sequence,
                                business_status=session.business_status,
                                phase=session.phase_at_start,
                                code=code,
                                message="Recovery preparation failed; original workspace retained",
                            ),
                        )
                    ],
                )
                raise ResumeRejected(
                    _rejection(
                        config,
                        code,
                        "Recovery preparation failed; original workspace retained",
                    )
                ) from exc
            return application, completed

        try:
            application, completed = await await_with_stop(
                prepare(), cancellation_token, interruption_token
            )
        except InterruptionRequested:
            assert (
                interruption_token is not None
                and interruption_token.deadline is not None
            )
            suspended = await execution.suspend(
                interruption_token.interruption, deadline=interruption_token.deadline
            )
            return ApplicationResult(run_result=suspended)
        return await application.resume(
            completed.record, cancellation_token, interruption_token=interruption_token
        )
