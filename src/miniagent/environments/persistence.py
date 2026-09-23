from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from miniagent.environments.config import (
    DockerOptions,
    LocalOptions,
    LocalWorkspaceSpec,
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
    options: LocalOptions
    environment_refs: dict[str, EnvironmentReference] = Field(default_factory=dict)


class PersistedDockerWorkspaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[WorkspaceKind.DOCKER] = WorkspaceKind.DOCKER
    run_id: UUID
    options: DockerOptions
    environment_refs: dict[str, EnvironmentReference] = Field(default_factory=dict)


type PersistedWorkspaceSpec = Annotated[
    PersistedLocalWorkspaceSpec | PersistedDockerWorkspaceSpec,
    Field(discriminator="kind"),
]


def persist_workspace_spec(
    spec: WorkspaceSpec,
    *,
    environment_refs: dict[str, EnvironmentReference] | None = None,
) -> PersistedWorkspaceSpec:
    references = environment_refs or {}
    if set(references) != set(spec.environment_values):
        raise ValueError(
            "Every workspace environment variable requires one persistent reference"
        )
    if isinstance(spec, LocalWorkspaceSpec):
        return PersistedLocalWorkspaceSpec(
            run_id=spec.run_id,
            options=spec.options,
            environment_refs=references,
        )
    return PersistedDockerWorkspaceSpec(
        run_id=spec.run_id,
        options=spec.options,
        environment_refs=references,
    )
