from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.tools.contracts import ToolCall


class SystemMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @model_validator(mode="after")
    def validate_content(self) -> "AssistantMessage":
        if self.content is None and not self.tool_calls:
            raise ValueError("Assistant message requires content or tool calls")
        return self


class ToolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["tool"] = "tool"
    content: str
    tool_call_id: str = Field(min_length=1, max_length=256)


type Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]
