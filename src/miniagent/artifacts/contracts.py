from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

if TYPE_CHECKING:
    from miniagent.artifacts.writer import ArtifactWriter


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: UUID
    uri: str
    type: str | None = None
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactPersistenceError(RuntimeError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


class ArtifactWrite(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    type: str
    name: str | None = None
    media_type: str = "application/octet-stream"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    artifact_id: UUID
    run_id: UUID
    type: str
    name: str | None = None
    media_type: str
    uri: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    def reference(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            uri=self.uri,
            type=self.type,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
        )


class ArtifactStore(Protocol):
    async def open_writer(
        self, request: ArtifactWrite, *, deadline: float | None = None
    ) -> "ArtifactWriter": ...

    async def put(
        self, request: ArtifactWrite, content: bytes, *, deadline: float | None = None
    ) -> ArtifactRef: ...

    async def read(self, ref: ArtifactRef) -> bytes: ...

    async def get_record(self, ref: ArtifactRef) -> ArtifactRecord: ...

    async def list_for_run(self, run_id: UUID) -> list[ArtifactRecord]: ...
