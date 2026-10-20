from miniagent.cli.application import ApplicationResult
from miniagent.cli.resume import ResumeConfig, resume_application
from miniagent.runtime import CancellationToken, RunResult
from miniagent.runtime.resume import ResumePlan
from tests.support.cli.resume import retained


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
    from tests.support.cli.bootstrap import configure_settings, create_source_repository

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
