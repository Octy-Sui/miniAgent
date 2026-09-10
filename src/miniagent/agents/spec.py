from pydantic import BaseModel, Field


class ModelTarget(BaseModel):
    provider: str
    model: str


class AgentSpec(BaseModel):
    name: str
    system_prompt: str
    model: ModelTarget
    allowed_tools: list[str] = Field(default_factory=list)
    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
