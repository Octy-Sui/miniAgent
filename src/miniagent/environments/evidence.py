from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.artifacts.contracts import ArtifactRef


class CommandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str
    timeout_seconds: float | None = None


class StreamEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: ArtifactRef | None
    received_bytes: int = Field(ge=0)
    status: Literal["complete", "partial", "missing"]
    eof_observed: bool
    reason: str | None = None

    @model_validator(mode="after")
    def validate_capture(self) -> "StreamEvidence":
        if self.status == "complete" and (not self.eof_observed or self.ref is None):
            raise ValueError(
                "Complete output requires observed EOF and a registered body"
            )
        if self.status == "missing" and self.ref is not None:
            raise ValueError("Missing output cannot reference a body")
        if self.ref is not None and self.ref.size_bytes != self.received_bytes:
            raise ValueError("Output size differs from registered body")
        return self


class CommandOutputEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: UUID
    execution_id: UUID
    stdout: StreamEvidence
    stderr: StreamEvidence
