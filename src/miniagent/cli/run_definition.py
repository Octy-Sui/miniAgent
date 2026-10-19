from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from miniagent.cli.model_limits import ModelLimits
from miniagent.environments.docker_connection import FrozenDockerConnection
from miniagent.environments.persistence import EnvironmentReference
from miniagent.evaluation.swebench_data import SWEbenchRecord, SWEbenchTaskContext
from miniagent.metering.pricing import PriceConfig
from miniagent.policy import digest_json
from miniagent.runtime.recovery import ApplicationDefinitionEnvelope
from miniagent.runtime.state import RunDefinition

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PositiveTimeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class _Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderReference(_Definition):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    endpoint_identity: str = Field(min_length=1)
    api_key_ref: EnvironmentReference
    endpoint_ref: EnvironmentReference

    @field_validator("endpoint_identity")
    @classmethod
    def nonsecret_endpoint(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint_identity must be a nonsecret HTTP endpoint")
        return value


class PassThroughDefinition(_Definition):
    kind: Literal["pass_through"] = "pass_through"
    schema_version: Literal[1] = 1


class SWEbenchDefinition(_Definition):
    kind: Literal["swebench"] = "swebench"
    schema_version: Literal[1] = 1
    task_context: SWEbenchTaskContext
    record: SWEbenchRecord
    record_sha256: Digest
    manifest_sha256: Digest
    tasks_sha256: Digest
    harness_version: Literal["5.0.2"]
    image_content_id: str = Field(min_length=1)
    original_image_name: str = Field(min_length=1)
    data_root: Path
    work_root: Path
    harness_executable: Path
    docker_executable: Path
    evaluation_connection: FrozenDockerConnection
    test_timeout_seconds: int = Field(gt=0)
    process_timeout_seconds: PositiveTimeout
    cleanup_timeout_seconds: PositiveTimeout
    artifact_timeout_seconds: PositiveTimeout
    allow_image_pull: bool

    @field_validator(
        "data_root", "work_root", "harness_executable", "docker_executable"
    )
    @classmethod
    def absolute_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Frozen evaluation paths must be absolute")
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "SWEbenchDefinition":
        if self.task_context.instance_id != self.record.instance_id:
            raise ValueError("Frozen task and record scope mismatch")
        if self.process_timeout_seconds <= self.test_timeout_seconds:
            raise ValueError("Harness process timeout must exceed test timeout")
        return self


class CLIApplicationDefinition(_Definition):
    schema_version: Literal[1] = 1
    pricing: PriceConfig
    pricing_sha256: Digest
    tool_contract_sha256: Digest
    model_limits: ModelLimits
    provider: ProviderReference
    artifact_root: Path
    model_limits_file: Path
    workspace_connection: FrozenDockerConnection | None
    evaluator: Annotated[
        PassThroughDefinition | SWEbenchDefinition, Field(discriminator="kind")
    ]
    candidate_submission_timeout_seconds: PositiveTimeout
    patch_export_cleanup_timeout_seconds: PositiveTimeout
    artifact_collection_timeout_seconds: PositiveTimeout
    suspension_timeout_seconds: PositiveTimeout = 30.0

    @field_validator("artifact_root", "model_limits_file")
    @classmethod
    def absolute_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Frozen application paths must be absolute")
        return value

    @model_validator(mode="after")
    def validate_pricing_and_budgets(self) -> "CLIApplicationDefinition":
        if digest_json(self.pricing.model_dump(mode="json")) != self.pricing_sha256:
            raise ValueError("Frozen pricing digest mismatch")
        if len(self.pricing.models) != 1 or (
            self.pricing.models[0].provider,
            self.pricing.models[0].model,
        ) != (self.provider.provider, self.provider.model):
            raise ValueError(
                "Frozen pricing must contain exactly the original provider/model"
            )
        if (
            self.candidate_submission_timeout_seconds
            <= self.patch_export_cleanup_timeout_seconds
        ):
            raise ValueError("Submission timeout must exceed cleanup reserve")
        if isinstance(self.evaluator, SWEbenchDefinition) != (
            self.workspace_connection is not None
        ):
            raise ValueError(
                "Docker workspace requires frozen SWE-bench evaluator and connection"
            )
        return self


def _validate_binding(
    value: CLIApplicationDefinition, definition: RunDefinition
) -> None:
    target = definition.agent_spec.model
    if (value.provider.provider, value.provider.model) != (
        target.provider,
        target.model,
    ):
        raise ValueError("Frozen provider/model does not match run definition")
    contract = {
        "definitions": [
            item.model_dump(mode="json") for item in definition.tool_definitions
        ],
        "metadata": list(definition.tool_contract_metadata),
    }
    if value.tool_contract_sha256 != digest_json(contract):
        raise ValueError("Frozen tool contract digest mismatch")


def freeze_application_definition(
    value: CLIApplicationDefinition, definition: RunDefinition
) -> ApplicationDefinitionEnvelope:
    value = CLIApplicationDefinition.model_validate_json(
        value.model_dump_json(by_alias=True)
    )
    _validate_binding(value, definition)
    payload = value.model_dump(mode="json", by_alias=True)
    return ApplicationDefinitionEnvelope(
        payload=payload,
        payload_sha256=digest_json(payload),
        run_definition_sha256=digest_json(definition.model_dump(mode="json")),
    )


def parse_application_definition(
    envelope: ApplicationDefinitionEnvelope, definition: RunDefinition
) -> CLIApplicationDefinition:
    envelope = ApplicationDefinitionEnvelope.model_validate(
        envelope.model_dump(mode="python")
    )
    if digest_json(envelope.payload) != envelope.payload_sha256:
        raise ValueError("Application payload digest mismatch")
    if (
        digest_json(definition.model_dump(mode="json"))
        != envelope.run_definition_sha256
    ):
        raise ValueError("Run definition digest mismatch")
    value = CLIApplicationDefinition.model_validate(envelope.payload)
    _validate_binding(value, definition)
    return value
