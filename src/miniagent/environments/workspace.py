# File   : workspace.py
# Created: 2026-09-11 18:42:44
# Author : octy Sui

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from miniagent.models.artifact import ArtifactRef
from miniagent.models.tool import WorkspaceDelta


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


class LocalWorkspaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[WorkspaceKind.LOCAL] = WorkspaceKind.LOCAL
    run_id: UUID
    source_dir: Path
    environment: dict[str, str] = Field(default_factory=dict)

    @field_validator("source_dir")
    @classmethod
    def resolve_source_dir(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class DockerWorkspaceSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal[WorkspaceKind.DOCKER] = WorkspaceKind.DOCKER
    run_id: UUID
    image: str = Field(min_length=1)
    workdir: str = "/testbed"
    user_policy: ContainerUserPolicy = ContainerUserPolicy.INHERIT
    environment: dict[str, str] = Field(default_factory=dict)
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


class Command(BaseModel):
    model_config = ConfigDict(frozen=True)

    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    environment: dict[str, str] = Field(default_factory=dict)
    stdin: str | None = None
    timeout_seconds: float = Field(default=60.0, gt=0)


class ExecutionStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ExecutionStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int = Field(ge=0)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    workspace_delta: WorkspaceDelta | None = None

    @model_validator(mode="after")
    def validate_exit_code(self) -> "ExecutionResult":
        if self.status is ExecutionStatus.SUCCESS and self.exit_code != 0:
            raise ValueError("Successful execution requires exit code 0")
        if self.status is ExecutionStatus.TIMEOUT and self.exit_code is not None:
            raise ValueError("Timed out execution cannot have an exit code")
        return self


class WorkspaceCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    checkpoint_id: str
    run_id: UUID
    workspace_id: str
    revision: str
    created_at: AwareDatetime
    image_id: str | None = None
    base_revision: str | None = None
    workdir: str | None = None
    artifact: ArtifactRef | None = None


class Patch(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    files: list[str] = Field(default_factory=list)
    base_revision: str | None = None


class Workspace(Protocol):
    @property
    def workspace_id(self) -> str: ...

    @property
    def run_id(self) -> UUID: ...

    async def execute(self, command: Command) -> ExecutionResult: ...

    async def checkpoint(self) -> WorkspaceCheckpoint: ...

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None: ...

    async def export_patch(self) -> Patch: ...

    async def destroy(self) -> None: ...


class WorkspaceBackend[SpecT](Protocol):
    async def create(self, spec: SpecT) -> Workspace: ...

    async def recover(
        self,
        spec: SpecT,
        checkpoint: WorkspaceCheckpoint,
    ) -> Workspace: ...


type WorkspaceSpec = Annotated[
    LocalWorkspaceSpec | DockerWorkspaceSpec,
    Field(discriminator="kind"),
]
