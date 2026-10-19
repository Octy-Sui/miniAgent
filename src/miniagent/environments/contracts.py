from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments.attachment import AttachmentFacts, WorkspaceAttachment


class WorkspaceDelta(BaseModel):
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)


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


class PatchExportCleanupError(RuntimeError):
    pass


class Workspace(Protocol):
    @property
    def workspace_id(self) -> str: ...

    @property
    def run_id(self) -> UUID: ...

    def describe_attachment(self) -> WorkspaceAttachment: ...

    async def execute(self, command: Command) -> ExecutionResult: ...

    async def checkpoint(self) -> WorkspaceCheckpoint: ...

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None: ...

    async def export_patch(
        self, *, timeout_seconds: float = 60.0, cleanup_timeout_seconds: float = 5.0
    ) -> Patch: ...

    async def destroy(self) -> None: ...


class WorkspaceBackend[SpecT, AttachmentT = WorkspaceAttachment](Protocol):
    async def inspect_attachment(self, attachment: AttachmentT) -> AttachmentFacts: ...

    async def attach(self, spec: SpecT, attachment: AttachmentT) -> Workspace: ...

    async def create(self, spec: SpecT) -> Workspace: ...

    async def recover(
        self,
        spec: SpecT,
        checkpoint: WorkspaceCheckpoint,
    ) -> Workspace: ...
