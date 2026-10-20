from pathlib import Path

import pytest

from miniagent.cli.resume import ResumeConfig, resume_application
from miniagent.runtime import CancellationToken
from miniagent.runtime.resume import ResumePlan, ResumeRejected
from tests.support.cli.bootstrap import configure_settings, create_source_repository
from tests.support.cli.resume import retained


@pytest.mark.parametrize(
    "change",
    [
        "missing_file",
        "missing_entry",
        "smaller_context",
        "smaller_output",
        "compatible",
    ],
)
async def test_resume_validates_current_capacity_without_replacing_frozen_values(
    tmp_path, monkeypatch, change
):
    configure_settings(tmp_path, monkeypatch)
    limits = tmp_path / "model_limits.toml"
    limits.write_bytes(Path("config/model_limits.toml").read_bytes())
    monkeypatch.setenv("MODEL_LIMITS_FILE", str(limits))
    application, _, provider = await retained(
        create_source_repository(tmp_path / "source"), monkeypatch
    )
    deps = application.dependencies
    before = await deps.run_store.get(deps.run_id)
    frozen = before.state.definition
    original_context = frozen.config.context_window_tokens
    original_output = frozen.agent_spec.generation.max_output_tokens
    if change == "missing_file":
        limits.unlink()
    elif change == "missing_entry":
        limits.write_text("models=[]\n")
    else:
        # New capacity supports the frozen request or invalidates exactly one bound.
        context = (
            original_context - 1
            if change == "smaller_context"
            else original_context + 1
        )
        output = (
            original_output - 1 if change == "smaller_output" else original_output + 1
        )
        limits.write_text(
            f'[[models]]\nprovider="{frozen.agent_spec.model.provider}"\nmodel="{frozen.agent_spec.model.model}"\ncontext_window_tokens={context}\nmax_output_tokens={output}\n'
        )
    config = ResumeConfig(
        run_id=deps.run_id, database=deps.run_store.path, dry_run=True
    )
    if change == "compatible":
        assert isinstance(
            await resume_application(config, cancellation_token=CancellationToken()),
            ResumePlan,
        )
        await resume_application(
            ResumeConfig(run_id=deps.run_id, database=deps.run_store.path),
            cancellation_token=CancellationToken(),
        )
        assert (await deps.run_store.get(deps.run_id)).state.definition == frozen
        assert len(provider.requests) == 1
    else:
        with pytest.raises(ResumeRejected, match="MODEL_CONFIG_MISMATCH"):
            await resume_application(config, cancellation_token=CancellationToken())
        assert await deps.run_store.get(deps.run_id) == before
        assert provider.requests == []
