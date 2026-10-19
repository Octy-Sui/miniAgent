from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from miniagent.environments.docker_connection import FrozenDockerConnection


class _Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["retained_workspace"] = "retained_workspace"
    run_id: UUID
    workspace_id: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)


class LocalAttachment(_Attachment):
    kind: Literal["local"] = "local"
    root: Path
    workspace_dir: Path
    repository_root: Path
    marker_run_id: UUID
    marker_workspace_id: str = Field(min_length=1)

    @field_validator("root", "workspace_dir", "repository_root")
    @classmethod
    def absolute_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Retained workspace paths must be absolute")
        return value


class DockerAttachment(_Attachment):
    kind: Literal["docker"] = "docker"
    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_id: str = Field(min_length=1)
    workdir: str = Field(pattern=r"^/")
    labels: dict[str, str]
    security_options: tuple[str, ...]
    resource_limits: dict[str, JsonValue]
    connection: FrozenDockerConnection


WorkspaceAttachment = Annotated[
    LocalAttachment | DockerAttachment, Field(discriminator="kind")
]


class AttachmentProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class AttachmentFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    workspace_id: str
    exists: bool
    identity_matches: bool | None
    container_status: str | None = None
    checked: tuple[str, ...] = ()
    live_checks: tuple[str, ...] = ()
    errors: tuple[AttachmentProblem, ...] = ()
