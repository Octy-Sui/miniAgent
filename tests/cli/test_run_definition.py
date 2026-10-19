from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.model_limits import ModelLimits
from miniagent.cli.run_definition import (
    CLIApplicationDefinition,
    PassThroughDefinition,
    ProviderReference,
    SWEbenchDefinition,
    freeze_application_definition,
    parse_application_definition,
)
from miniagent.cli.settings import RunLocationSettings
from miniagent.environments.attachment import LocalAttachment
from miniagent.environments.docker_connection import FrozenDockerConnection
from miniagent.environments.persistence import EnvironmentReference
from miniagent.evaluation.swebench_data import SWEbenchTaskContext, load_prepared_task
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.policy import digest_json
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.recovery import ApplicationDefinitionEnvelope
from miniagent.runtime.state import create_run_state


def definition():
    return create_run_state(
        run_id=uuid4(), task="task", agent_spec=DEFAULT_AGENT, config=RuntimeConfig()
    ).definition


def frozen_application(tmp_path: Path, evaluator):
    target = DEFAULT_AGENT.model
    pricing = PriceConfig(
        models=[
            ModelPrice(
                provider=target.provider,
                model=target.model,
                input_per_token="0.000001",
                output_per_token="0.000002",
            )
        ]
    )
    return CLIApplicationDefinition(
        pricing=pricing,
        pricing_sha256=digest_json(pricing.model_dump(mode="json")),
        tool_contract_sha256=digest_json({"definitions": [], "metadata": []}),
        model_limits=ModelLimits(context_window_tokens=32768, max_output_tokens=4096),
        provider=ProviderReference(
            provider=target.provider,
            model=target.model,
            protocol_version="qwen-chat-completions-v1",
            endpoint_identity="https://example.test/v1",
            api_key_ref=EnvironmentReference(key="OPENAI_API_KEY"),
            endpoint_ref=EnvironmentReference(key="OPENAI_BASE_URL"),
        ),
        artifact_root=tmp_path / "artifacts",
        model_limits_file=tmp_path / "limits.toml",
        workspace_connection=evaluator.evaluation_connection
        if isinstance(evaluator, SWEbenchDefinition)
        else None,
        evaluator=evaluator,
        candidate_submission_timeout_seconds=60,
        patch_export_cleanup_timeout_seconds=5,
        artifact_collection_timeout_seconds=30,
    )


@pytest.mark.parametrize(("backend",), [("local",), ("docker",)])
def test_frozen_definition_round_trip_and_digest(tmp_path, prepared_bundle, backend):
    """TC-U2-09/10, U13: retain full evaluator identity and immutable definition binding."""
    evaluator = PassThroughDefinition()
    if backend == "docker":
        context = SWEbenchTaskContext(instance_id="django__django-11477")
        prepared = load_prepared_task(prepared_bundle, context)
        evaluator = SWEbenchDefinition(
            task_context=context,
            record=prepared.record,
            record_sha256=prepared.record_sha256,
            manifest_sha256=prepared.manifest_sha256,
            tasks_sha256=prepared.tasks_sha256,
            harness_version="5.0.2",
            image_content_id="sha256:" + "a" * 64,
            original_image_name=prepared.actual_image,
            data_root=prepared_bundle,
            work_root=tmp_path / "evaluation",
            harness_executable=tmp_path / "swebench",
            docker_executable=tmp_path / "docker",
            evaluation_connection=FrozenDockerConnection(
                source_context=None,
                endpoint="unix:///tmp/a.sock",
                cert_path=None,
                tls_verify=False,
                daemon_id="a",
            ),
            test_timeout_seconds=1800,
            process_timeout_seconds=2400,
            cleanup_timeout_seconds=30,
            artifact_timeout_seconds=30,
            allow_image_pull=False,
        )
    frozen = frozen_application(tmp_path, evaluator)
    run_definition = definition()
    envelope = freeze_application_definition(frozen, run_definition)
    loaded = ApplicationDefinitionEnvelope.model_validate_json(
        envelope.model_dump_json()
    )
    assert parse_application_definition(loaded, run_definition) == frozen
    assert frozen.artifact_root.is_absolute()
    assert frozen.suspension_timeout_seconds == 30.0
    assert "secret-resume-fixture" not in envelope.model_dump_json()


@pytest.mark.parametrize(
    ("mutation",), [("payload",), ("run",), ("provider",), ("pricing",), ("tools",)]
)
def test_frozen_definition_rejects_scope_or_digest_mismatch(tmp_path, mutation):
    frozen = frozen_application(tmp_path, PassThroughDefinition())
    run_definition = definition()
    envelope = freeze_application_definition(frozen, run_definition)
    if mutation == "payload":
        envelope = envelope.model_copy(
            update={"payload": dict(envelope.payload, artifact_root="/other")}
        )
    elif mutation == "run":
        run_definition = run_definition.model_copy(
            update={"config": RuntimeConfig(max_steps=2)}
        )
    else:
        payload = dict(envelope.payload)
        if mutation == "provider":
            provider = payload["provider"]
            assert isinstance(provider, dict)
            payload["provider"] = dict(provider, model="other")
        elif mutation == "pricing":
            payload["pricing_sha256"] = "0" * 64
        else:
            payload["tool_contract_sha256"] = "0" * 64
        envelope = envelope.model_copy(
            update={"payload": payload, "payload_sha256": digest_json(payload)}
        )
    with pytest.raises(ValueError, match="digest|provider|pricing|tool"):
        parse_application_definition(envelope, run_definition)


def test_record_location_does_not_require_provider_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RUN_DATABASE_PATH", "relative/runs.sqlite3")
    assert (
        RunLocationSettings(_env_file=None).run_database_path
        == Path("relative/runs.sqlite3").resolve()
    )


def test_attachment_requires_absolute_original_paths(tmp_path):
    with pytest.raises(ValidationError, match="absolute"):
        LocalAttachment(
            run_id=uuid4(),
            workspace_id="workspace",
            base_revision="base",
            root=Path("relative"),
            workspace_dir=tmp_path,
            repository_root=tmp_path,
            marker_run_id=uuid4(),
            marker_workspace_id="workspace",
        )


@pytest.mark.parametrize(
    ("endpoint",),
    [
        ("https://user:secret-resume-fixture@example.test/v1",),
        ("https://example.test/v1?api_key=secret-resume-fixture",),
    ],
)
def test_provider_identity_cannot_persist_credentials(endpoint):
    with pytest.raises(ValidationError, match="endpoint_identity"):
        ProviderReference(
            provider="qwen",
            model="model",
            protocol_version="qwen-chat-completions-v1",
            endpoint_identity=endpoint,
            api_key_ref=EnvironmentReference(key="OPENAI_API_KEY"),
            endpoint_ref=EnvironmentReference(key="OPENAI_BASE_URL"),
        )
