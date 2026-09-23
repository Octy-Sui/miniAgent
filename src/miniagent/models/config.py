from pydantic import BaseModel, ConfigDict, Field


class ModelTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float | None = None
    max_output_tokens: int | None = Field(default=None, ge=1)
