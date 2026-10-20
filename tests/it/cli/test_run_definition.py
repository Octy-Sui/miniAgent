import pytest

from miniagent.cli.run_definition import (
    PassThroughDefinition,
    SWEbenchDefinition,
    freeze_application_definition,
    parse_application_definition,
)
from miniagent.environments.docker_connection import FrozenDockerConnection
from miniagent.evaluation.swebench_data import SWEbenchTaskContext, load_prepared_task
from miniagent.policy import digest_json
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.recovery import ApplicationDefinitionEnvelope
from tests.support.cli.run_definition import definition, frozen_application


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
