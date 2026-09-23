from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    cpu_count: float = Field(default=1.0, gt=0)
    memory_mb: int = Field(default=1024, ge=64)
    pids: int = Field(default=256, ge=1)


class ContainerUserPolicy(StrEnum):
    INHERIT = "inherit"
    REQUIRE_NON_ROOT = "require_non_root"


class WorkspaceKind(StrEnum):
    LOCAL = "local"
    DOCKER = "docker"


class LocalOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_dir: Path

    @field_validator("source_dir")
    @classmethod
    def resolve_source_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class DockerOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    image: str = Field(min_length=1)
    workdir: str = "/testbed"
    user_policy: ContainerUserPolicy = ContainerUserPolicy.INHERIT
    network_enabled: bool = False
    limits: ResourceLimits = Field(default_factory=ResourceLimits)

    @field_validator("workdir")
    @classmethod
    def validate_workdir(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute():
            raise ValueError("Docker workspace directory must be absolute")
        if path == PurePosixPath("/"):
            raise ValueError("Docker workspace directory cannot be the filesystem root")
        if ".." in path.parts:
            raise ValueError("Docker workspace directory cannot contain '..'")
        return str(path)


class LocalWorkspaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[WorkspaceKind.LOCAL] = WorkspaceKind.LOCAL
    run_id: UUID
    options: LocalOptions
    environment_values: dict[str, str] = Field(default_factory=dict)


class DockerWorkspaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[WorkspaceKind.DOCKER] = WorkspaceKind.DOCKER
    run_id: UUID
    options: DockerOptions
    environment_values: dict[str, str] = Field(default_factory=dict)


type WorkspaceSpec = Annotated[
    LocalWorkspaceSpec | DockerWorkspaceSpec,
    Field(discriminator="kind"),
]
