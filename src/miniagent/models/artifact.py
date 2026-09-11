# File   : artifact.py
# Created: 2026-09-11 18:43:18
# Author : octy Sui

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    uri: str
    type: str | None = None
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
