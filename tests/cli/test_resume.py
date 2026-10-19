from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.application import ApplicationResult
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.cli.resume import ResumeConfig, resume_application
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.runtime import CancellationToken, RunResult
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.resume import ResumePlan
from miniagent.runtime.state import create_run_state
from tests.runtime.test_runtime import QueueProvider, final_response


async def retained(configured_local, monkeypatch, *, old_contract=False):
    from miniagent.cli import bootstrap as module
    from miniagent.models.protocol import ModelUsage

    provider = QueueProvider(
        [
            final_response("done").model_copy(
                update={
                    "usage": ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                }
            )
        ]
    )
    monkeypatch.setattr(module, "build_provider", lambda agent, settings: provider)
    application = await bootstrap(
        BootstrapConfig(agent=DEFAULT_AGENT, task="task", source_dir=configured_local)
    )
    deps = application.dependencies
    state = create_run_state(
        run_id=deps.run_id,
        task=deps.task,
        agent_spec=deps.agent,
        config=deps.config,
        messages=(
            SystemMessage(content=deps.agent.system_prompt),
            UserMessage(content=deps.task),
        ),
    )
    state = state.model_copy(
        update={
            "definition": state.definition.model_copy(
                update={
                    "tool_definitions": tuple(
                        deps.tools.definitions(deps.agent.allowed_tools)
                    ),
                    "tool_contract_metadata": tuple(
                        deps.tools.contract_metadata(name)
                        for name in deps.agent.allowed_tools
                    ),
                }
            )
        }
    )
    context = deps.recovery_context
    if old_contract:
        assert context.application is not None
        from miniagent.cli.run_definition import freeze_application_definition
        from miniagent.policy import digest_json

        metadata = tuple(
            dict(item, version="older")
            for item in state.definition.tool_contract_metadata
        )
        definition = state.definition.model_copy(
            update={"tool_contract_metadata": metadata}
        )
        state = state.model_copy(update={"definition": definition})
        from miniagent.cli.run_definition import CLIApplicationDefinition

        application_definition = CLIApplicationDefinition.model_validate(
            context.application.payload
        )
        application_definition = application_definition.model_copy(
            update={
                "tool_contract_sha256": digest_json(
                    {
                        "definitions": [
                            item.model_dump(mode="json")
                            for item in definition.tool_definitions
                        ],
                        "metadata": list(metadata),
                    }
                )
            }
        )
        context = context.model_copy(
            update={
                "application": freeze_application_definition(
                    application_definition, definition
                )
            }
        )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=deps.run_id,
                store=deps.run_store,
                recovery_context=context,
            )
        ),
    )
    await execution.begin_step(
        provider=deps.agent.model.provider, model=deps.agent.model.model
    )
    return application, execution, provider


async def test_resume_uses_completed_commit_record(resume_case):
    application, _execution, provider = resume_case
    deps = application.dependencies
    config = ResumeConfig(run_id=deps.run_id, database=deps.run_store.path)
    result = await resume_application(config, cancellation_token=CancellationToken())

    assert isinstance(result, ApplicationResult)
    assert isinstance(result.run_result, RunResult)
    assert result.run_result.output == "done"
    events = await deps.run_store.list_events(deps.run_id)
    assert sum(event.type.value == "run_started" for event in events) == 1
    assert sum(event.type.value == "step_started" for event in events) == 1
    assert len(provider.requests) == 1
    assert result.run_result.usage.unconfirmed_model_requests == 1


async def test_dry_run_needs_no_provider_key_or_new_directories(
    resume_case, monkeypatch
):
    application, _execution, provider = resume_case
    deps = application.dependencies
    before = await deps.run_store.get(deps.run_id)
    monkeypatch.delenv("OPENAI_API_KEY")
    result = await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path, dry_run=True),
        cancellation_token=CancellationToken(),
    )
    assert isinstance(result, ResumePlan)
    assert await deps.run_store.get(deps.run_id) == before
    assert provider.requests == []


async def test_changed_endpoint_is_audited_without_secrets(resume_case, monkeypatch):
    application, _execution, _provider = resume_case
    deps = application.dependencies
    monkeypatch.setenv(
        "OPENAI_BASE_URL", "https://user:secret@other.example/v1?token=private"
    )
    await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
        cancellation_token=CancellationToken(),
    )
    events = await deps.run_store.list_events(deps.run_id)
    changed = [
        event for event in events if event.type.value == "provider_identity_changed"
    ]
    assert len(changed) == 1
    assert changed[0].payload.current_endpoint_identity == "https://other.example/v1"
    assert (
        "secret" not in changed[0].model_dump_json()
        and "private" not in changed[0].model_dump_json()
    )


async def test_dry_run_rejects_valid_frozen_older_tool_contract(tmp_path, monkeypatch):
    import pytest

    from miniagent.runtime.resume import ResumeRejected
    from tests.cli.test_bootstrap import configure_settings, create_source_repository

    configure_settings(tmp_path, monkeypatch)
    application, _execution, provider = await retained(
        create_source_repository(tmp_path / "source"), monkeypatch, old_contract=True
    )
    deps = application.dependencies
    with pytest.raises(ResumeRejected, match="TOOL_CONTRACT_MISMATCH"):
        await resume_application(
            ResumeConfig(
                run_id=deps.run_id, database=deps.run_store.path, dry_run=True
            ),
            cancellation_token=CancellationToken(),
        )
    assert provider.requests == []


async def test_resume_loads_dotenv_credentials_and_audits_dotenv_endpoint(
    resume_case, tmp_path, monkeypatch
):

    application, _execution, provider = resume_case
    deps = application.dependencies
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.delenv("OPENAI_BASE_URL")
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=dotenv-secret\n"
        "OPENAI_BASE_URL=https://user:password@dotenv.example/v1?token=private\n"
        "CANDIDATE_SUBMISSION_TIMEOUT_SECONDS=invalid-current-setting\n"
    )
    result = await resume_application(
        ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
        cancellation_token=CancellationToken(),
    )

    assert isinstance(result, ApplicationResult)
    assert isinstance(result.run_result, RunResult)
    assert result.run_result.output == "done"
    assert len(provider.requests) == 1
    events = await deps.run_store.list_events(deps.run_id)
    changed = [e for e in events if e.type.value == "provider_identity_changed"]
    assert len(changed) == 1
    assert changed[0].payload.current_endpoint_identity == "https://dotenv.example/v1"
    assert all(
        secret not in changed[0].model_dump_json()
        for secret in ("dotenv-secret", "password", "private")
    )
