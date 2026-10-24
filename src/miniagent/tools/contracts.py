from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments.contracts import WorkspaceDelta

if TYPE_CHECKING:
    from miniagent.models.messages import ToolMessage


class ToolDefinition(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not value or len(value.encode("utf-8")) > 256:
            raise ValueError("Tool call ID must contain 1 to 256 UTF-8 bytes")
        return value


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"


class ToolArgumentIssue(BaseModel):
    location: list[str | int] = Field(default_factory=list)
    code: str
    message: str


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    issues: list[ToolArgumentIssue] = Field(default_factory=list)


class ToolOutputInfo(BaseModel):
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    content_truncated: bool = False
    omitted_fields: list[str] = Field(default_factory=list)

    @field_validator("omitted_fields")
    @classmethod
    def validate_omitted_fields(cls, values: list[str]) -> list[str]:
        allowed = {
            "data",
            "metrics",
            "workspace_delta",
            "artifacts",
            "error.issues",
            "error.message",
        }
        if len(values) != len(set(values)) or not set(values) <= allowed:
            raise ValueError("Tool output contains invalid omitted fields")
        return values


class ToolOutputLimits(BaseModel):
    max_content_bytes: int = Field(default=16 * 1024, ge=1)
    max_message_bytes: int = Field(default=32 * 1024, ge=2048)

    @model_validator(mode="after")
    def validate_limits(self) -> "ToolOutputLimits":
        if self.max_content_bytes > self.max_message_bytes:
            raise ValueError("Tool content limit cannot exceed message limit")
        return self


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    status: ToolStatus
    duration_ms: int | None = Field(default=None, ge=0)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    error: ToolError | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    workspace_delta: WorkspaceDelta | None = None
    output: ToolOutputInfo = Field(default_factory=ToolOutputInfo)

    def to_response_item(self, limits: ToolOutputLimits | None = None) -> "ToolMessage":
        from miniagent.models.messages import ToolMessage
        from miniagent.tools.rendering import render_tool_result

        return ToolMessage(
            tool_call_id=self.tool_call_id,
            content=render_tool_result(self, limits or ToolOutputLimits()),
        )

    def tool_result_metadata(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")

    @model_validator(mode="after")
    def validate_error(self) -> "ToolResult":
        if self.status is ToolStatus.SUCCESS and self.error is not None:
            raise ValueError("Successful tool result cannot contain an error")
        if self.status is not ToolStatus.SUCCESS and self.error is None:
            raise ValueError("Unsuccessful tool result requires an error")
        return self


class ToolReturnValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: Literal["accepted", "rejected", "not_returned"]
    reason: str | None = None


class InlineToolResultEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["inline"] = "inline"
    result: ToolResult


class ArtifactToolResultEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["artifact"] = "artifact"
    ref: ArtifactRef


type ToolResultEvidence = Annotated[
    InlineToolResultEvidence | ArtifactToolResultEvidence, Field(discriminator="kind")
]
