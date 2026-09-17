from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from miniagent.environments import (
    ContainerUserPolicy,
    DockerWorkspaceSpec,
    LocalWorkspaceSpec,
    ResourceLimits,
    WorkspaceCheckpoint,
    WorkspaceKind,
    WorkspaceSpec,
)


class EnvironmentReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["process_env"] = "process_env"
    key: str = Field(min_length=1)


class PersistedLocalWorkspaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[WorkspaceKind.LOCAL] = WorkspaceKind.LOCAL
    run_id: UUID
    source_dir: Path
    environment_refs: dict[str, EnvironmentReference] = Field(default_factory=dict)

    @field_validator("source_dir")
    @classmethod
    def resolve_source_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class PersistedDockerWorkspaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[WorkspaceKind.DOCKER] = WorkspaceKind.DOCKER
    run_id: UUID
    image: str = Field(min_length=1)
    workdir: str = "/testbed"
    user_policy: ContainerUserPolicy = ContainerUserPolicy.INHERIT
    environment_refs: dict[str, EnvironmentReference] = Field(default_factory=dict)
    network_enabled: bool = False
    limits: ResourceLimits = Field(default_factory=ResourceLimits)


PersistedWorkspaceSpec = Annotated[
    PersistedLocalWorkspaceSpec | PersistedDockerWorkspaceSpec,
    Field(discriminator="kind"),
]


def persist_workspace_spec(
    spec: WorkspaceSpec,
    *,
    environment_refs: dict[str, EnvironmentReference] | None = None,
) -> PersistedWorkspaceSpec:
    references = environment_refs or {}
    if set(references) != set(spec.environment):
        raise ValueError(
            "Every workspace environment variable requires one persistent reference"
        )
    if isinstance(spec, LocalWorkspaceSpec):
        return PersistedLocalWorkspaceSpec(
            run_id=spec.run_id,
            source_dir=spec.source_dir,
            environment_refs=references,
        )
    return PersistedDockerWorkspaceSpec(
        run_id=spec.run_id,
        image=spec.image,
        workdir=spec.workdir,
        user_policy=spec.user_policy,
        environment_refs=references,
        network_enabled=spec.network_enabled,
        limits=spec.limits,
    )


class RunRecoveryContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    workspace_id: str = Field(min_length=1)
    workspace_spec: PersistedWorkspaceSpec
    checkpoint: WorkspaceCheckpoint | None = None
    policy_revision: str | None = None
    pricing_revision: str | None = None

    @field_validator("workspace_spec", mode="before")
    @classmethod
    def reject_runtime_workspace_spec(cls, value: object) -> object:
        if isinstance(value, (LocalWorkspaceSpec, DockerWorkspaceSpec)):
            raise ValueError(
                "Recovery context requires a sanitized persisted workspace spec"
            )
        return value

    @model_validator(mode="after")
    def validate_scope(self) -> "RunRecoveryContext":
        if (
            self.checkpoint is not None
            and self.workspace_spec.run_id != self.checkpoint.run_id
        ):
            raise ValueError(
                "Recovery checkpoint and workspace must belong to the same run"
            )
        if (
            self.checkpoint is not None
            and self.checkpoint.workspace_id != self.workspace_id
        ):
            raise ValueError("Recovery checkpoint must identify the current workspace")
        return self
