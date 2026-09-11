# File   : store.py
# Created: 2026-09-11 18:42:27
# Author : octy Sui

from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from miniagent.models.artifact import ArtifactRef


class ArtifactNotFoundError(LookupError):
    pass


class ArtifactWrite(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    type: str
    name: str | None = None
    media_type: str = "application/octet-stream"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ArtifactStore(Protocol):
    async def put(self, request: ArtifactWrite, content: bytes) -> ArtifactRef: ...

    async def read(self, ref: ArtifactRef) -> bytes: ...
