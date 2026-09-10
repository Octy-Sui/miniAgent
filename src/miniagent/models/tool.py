# File   : tool.py
# Created: 2026-09-09 15:02:15
# Author : octy Sui

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ToolStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    DENIED = "denied"


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ArtifactRef(BaseModel):
    uri: str
    type: str | None = None


class WorkspaceDelta(BaseModel):
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)


class ToolResult(BaseModel):
    tool_call_id: str

    status: ToolStatus

    content: str

    data: dict[str, Any] | None = None

    error: ToolError | None = None

    artifacts: list[ArtifactRef] = Field(default_factory=list)

    metrics: dict[str, Any] = Field(default_factory=dict)

    workspace_delta: WorkspaceDelta | None = None
