from uuid import uuid4

import pytest

from miniagent.environments.attachment import AttachmentFacts, LocalAttachment
from miniagent.environments.config import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    HostProcessIdentity,
)
from miniagent.environments.persistence import persist_workspace_spec
from miniagent.runtime import RuntimeConfig, create_run_state
from miniagent.runtime.progress import CurrentStep, ExecutionPhase, OperationRef
from miniagent.runtime.recovery import ApplicationDefinitionEnvelope, RunRecoveryContext
from miniagent.runtime.resume import (
    ExecutionChainFact,
    ResumeEnvironmentFacts,
    ResumeHistoryFacts,
    ResumePlan,
    ResumeRejection,
    plan_resume,
)
from miniagent.runtime.state import ToolBatch
from miniagent.runtime.store_contracts import RunRecord
from miniagent.tools.contracts import ToolCall
from tests.runtime.test_runtime import agent


def case(tmp_path, name=None, readonly=False):
    run_id = uuid4()
    state = create_run_state(
        run_id=run_id, task="task", agent_spec=agent(), config=RuntimeConfig()
    )
    if name:
        call = ToolCall(id="call", name=name, arguments={})
        operation = OperationRef(
            kind="tool",
            attempt_id=uuid4(),
            started_event_id=uuid4(),
            started_sequence=2,
            step_number=1,
            call_index=0,
            tool_call_id="call",
        )
        execution = state.execution.model_copy(
            update={
                "phase": ExecutionPhase.EXECUTE_TOOLS,
                "current_step": CurrentStep(
                    step_number=1,
                    message_start=0,
                    started_event_id=uuid4(),
                    started_sequence=1,
                ),
                "steps_started": 1,
                "tool_batch": ToolBatch(calls=(call,)),
                "active_operation": operation,
            }
        )
        state = state.model_copy(
            update={
                "execution": execution,
                "definition": state.definition.model_copy(
                    update={
                        "tool_contract_metadata": (
                            {
                                "name": name,
                                "unknown_outcome": "retry_read_only"
                                if readonly
                                else "reject",
                            },
                        )
                    }
                ),
            }
        )
    attachment = LocalAttachment(
        run_id=run_id,
        workspace_id="workspace",
        base_revision="base",
        root=tmp_path,
        workspace_dir=tmp_path,
        repository_root=tmp_path,
        marker_run_id=run_id,
        marker_workspace_id="workspace",
    )
    spec = LocalWorkspaceSpec(run_id=run_id, options=LocalOptions(source_dir=tmp_path))
    context = RunRecoveryContext(
        workspace_id="workspace",
        workspace_spec=persist_workspace_spec(spec),
        attachment=attachment,
        application=ApplicationDefinitionEnvelope(
            payload={}, payload_sha256="0" * 64, run_definition_sha256="0" * 64
        ),
    )
    record = RunRecord(
        state=state,
        last_event_sequence=2,
        latest_snapshot_version=1,
        recovery_context=context,
    )
    env = ResumeEnvironmentFacts(
        attachment=AttachmentFacts(
            run_id=run_id, workspace_id="workspace", exists=True, identity_matches=True
        )
    )
    return record, env


def ended(record):
    operation = record.state.execution.active_operation
    identity = HostProcessIdentity(
        attempt_id=operation.attempt_id,
        execution_id=uuid4(),
        role="local_command",
        pid=123,
        create_time=1,
        host_boot_id="boot",
        pgid=123,
        sid=123,
    )
    evidence = ExecutionEvidence(
        execution_id=identity.execution_id,
        identity=identity,
        outcome="ended",
        started_observed=True,
        group_ended=True,
        issuer_ended=True,
    )
    chain = ExecutionChainFact(
        attempt_id=operation.attempt_id,
        identities=(identity,),
        create_permission_committed=False,
        start_permission_committed=False,
        command_permission_committed=True,
        evidence=(evidence,),
        scope="business",
    )
    return ResumeHistoryFacts(execution_chains=(chain,))


@pytest.mark.parametrize(("name",), [("shell",), ("plugin",)])
def test_unknown_tool_rejects_even_when_execution_ended(tmp_path, name):
    record, env = case(tmp_path, name)
    rejection = plan_resume(record, ended(record), env)
    assert isinstance(rejection, ResumeRejection)
    assert rejection.code == "UNCERTAIN_TOOL_EXECUTION"
    assert rejection.step_number == 1 and rejection.call_index == 0


def test_read_only_ended_retries_new_attempt(tmp_path):
    record, env = case(tmp_path, "read_file", True)
    plan = plan_resume(record, ended(record), env)
    assert isinstance(plan, ResumePlan)
    assert plan.close_operations == (
        record.state.execution.active_operation.attempt_id,
    )
    assert plan.source_sequence == record.last_event_sequence
    assert plan.phase == record.state.execution.phase


def test_read_only_missing_identity_is_busy(tmp_path):
    record, env = case(tmp_path, "read_file", True)
    assert (
        rejection_code(plan_resume(record, ResumeHistoryFacts(), env))
        == "WORKSPACE_BUSY"
    )


@pytest.mark.parametrize(("count",), [(0,), (3,), (4,), (100,)])
def test_resume_count_is_audit_only(tmp_path, count):
    record, env = case(tmp_path)
    record = record.model_copy(
        update={
            "state": record.state.model_copy(
                update={
                    "recovery": record.state.recovery.model_copy(
                        update={"resume_attempts": count}
                    )
                }
            )
        }
    )
    assert isinstance(plan_resume(record, ResumeHistoryFacts(), env), ResumePlan)


def test_rejection_priority_is_stable(tmp_path):
    record, env = case(tmp_path, "shell")
    env = ResumeEnvironmentFacts(
        attachment=env.attachment.model_copy(update={"exists": False}),
        dependency_rejections=(
            ResumeRejection(
                code="MODEL_CONFIG_MISMATCH",
                run_id=record.state.identity.run_id,
                message="model",
            ),
        ),
    )
    assert (
        rejection_code(plan_resume(record, ResumeHistoryFacts(), env))
        == "UNCERTAIN_TOOL_EXECUTION"
    )
    assert (
        rejection_code(
            plan_resume(
                record, ResumeHistoryFacts(history_errors=("missing history",)), env
            )
        )
        == "RESUME_STATE_INCONSISTENT"
    )
    record = record.model_copy(update={"recovery_context": None})
    assert (
        rejection_code(plan_resume(record, ResumeHistoryFacts(), env))
        == "RESUME_METADATA_MISSING"
    )


def test_pending_start_stays_busy_after_helper_exit(tmp_path):
    from miniagent.environments.managed_execution import DockerExecIdentity

    record, env = case(tmp_path, "read_file", True)
    history = ended(record)
    chain = history.execution_chains[0]
    identity = DockerExecIdentity(
        attempt_id=chain.attempt_id,
        execution_id=uuid4(),
        helper_execution_id=chain.identities[0].execution_id,
        daemon_id="d",
        container_id="c",
        exec_id="e",
        start_permitted=True,
        started_observed=False,
    )
    remote = ExecutionEvidence(
        execution_id=identity.execution_id,
        identity=identity,
        outcome="ended",
        started_observed=False,
        group_ended=True,
        issuer_ended=True,
    )
    chain = ExecutionChainFact(
        attempt_id=chain.attempt_id,
        identities=(*chain.identities, identity),
        create_permission_committed=True,
        start_permission_committed=True,
        command_permission_committed=True,
        evidence=(*chain.evidence, remote),
        scope="business",
    )
    assert (
        rejection_code(
            plan_resume(record, ResumeHistoryFacts(execution_chains=(chain,)), env)
        )
        == "WORKSPACE_BUSY"
    )


def rejection_code(result):
    assert isinstance(result, ResumeRejection)
    return result.code


@pytest.mark.parametrize(
    ("fault", "expected"),
    [
        ("terminal", "RUN_NOT_RESUMABLE"),
        ("missing", "WORKSPACE_MISSING"),
        ("dependency", "MODEL_CONFIG_MISMATCH"),
        ("candidate", "CANDIDATE_ARTIFACT_INVALID"),
        ("evaluation", "EVALUATION_ATTEMPT_BUSY"),
    ],
)
def test_adjacent_priority_groups(tmp_path, fault, expected):
    from dataclasses import replace

    from miniagent.runtime.state import RunStatus

    record, env = case(tmp_path)
    rejection = lambda code: ResumeRejection(
        code=code, run_id=record.state.identity.run_id, message=code
    )
    env = replace(env, evaluation_rejection=rejection("EVALUATION_ATTEMPT_BUSY"))
    if fault != "evaluation":
        env = replace(env, candidate_rejection=rejection("CANDIDATE_ARTIFACT_INVALID"))
    if fault in ("terminal", "missing", "dependency"):
        env = replace(env, dependency_rejections=(rejection("MODEL_CONFIG_MISMATCH"),))
    if fault in ("terminal", "missing"):
        env = replace(
            env, attachment=env.attachment.model_copy(update={"exists": False})
        )
    if fault == "terminal":
        record = record.model_copy(
            update={
                "recovery_context": None,
                "state": record.state.model_copy(
                    update={
                        "lifecycle": record.state.lifecycle.model_copy(
                            update={"status": RunStatus.FAILED}
                        )
                    }
                ),
            }
        )
    assert rejection_code(plan_resume(record, ResumeHistoryFacts(), env)) == expected


@pytest.mark.parametrize(("outcome",), [("active",), ("unknown",)])
def test_read_only_active_or_unknown_group_rejects(tmp_path, outcome):
    from dataclasses import replace

    record, env = case(tmp_path, "read_file", True)
    history = ended(record)
    chain = history.execution_chains[0]
    evidence = chain.evidence[0].model_copy(
        update={"outcome": outcome, "group_ended": False}
    )
    history = replace(history, execution_chains=(replace(chain, evidence=(evidence,)),))
    assert rejection_code(plan_resume(record, history, env)) == "WORKSPACE_BUSY"


def test_state_cursor_requires_exact_committed_tool_prefix(tmp_path):
    record, env = case(tmp_path, "read_file", True)
    state = record.state
    batch = state.execution.tool_batch
    state = state.model_copy(
        update={
            "execution": state.execution.model_copy(
                update={
                    "tool_batch": batch.model_copy(update={"next_index": 1}),
                    "phase": ExecutionPhase.COMPLETE_STEP,
                    "active_operation": None,
                }
            )
        }
    )
    record = record.model_copy(update={"state": state})
    assert (
        rejection_code(plan_resume(record, ResumeHistoryFacts(), env))
        == "RESUME_STATE_INCONSISTENT"
    )


def test_guest_group_check_can_defer_only_after_known_helper_end(tmp_path):
    from dataclasses import replace

    from miniagent.environments.managed_execution import (
        DockerExecIdentity,
        GuestProcessIdentity,
    )

    record, env = case(tmp_path, "read_file", True)
    history = ended(record)
    chain = history.execution_chains[0]
    remote = DockerExecIdentity(
        attempt_id=chain.attempt_id,
        execution_id=uuid4(),
        helper_execution_id=chain.identities[0].execution_id,
        daemon_id="daemon",
        container_id="container",
        exec_id="exec",
        start_permitted=True,
        started_observed=True,
        guest=GuestProcessIdentity(
            pid=123, pgid=123, sid=123, boot_id="guest", start_ticks=1
        ),
    )
    chain = replace(chain, identities=(*chain.identities, remote))
    history = replace(history, execution_chains=(chain,))
    env = replace(
        env,
        attachment=env.attachment.model_copy(
            update={"live_checks": ("guest_execution_groups",)}
        ),
    )
    result = plan_resume(record, history, env)
    assert isinstance(result, ResumePlan) and result.readiness == "requires_live_checks"
    active = ExecutionEvidence(
        execution_id=remote.execution_id,
        identity=remote,
        outcome="active",
        started_observed=True,
        group_ended=False,
        issuer_ended=True,
    )
    assert (
        rejection_code(
            plan_resume(record, history, replace(env, execution_evidence=(active,)))
        )
        == "WORKSPACE_BUSY"
    )
