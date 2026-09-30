from pathlib import Path
import tomllib

from pydantic import BaseModel, ConfigDict, Field


class ModelLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class _ModelEntry(ModelLimits):
    provider: str
    model: str


class _LimitsFile(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    models: list[_ModelEntry]


def load_model_limits(path: Path, provider: str, model: str) -> ModelLimits:
    entries = _LimitsFile.model_validate(tomllib.loads(path.read_text())).models
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.provider, entry.model)
        if key in seen:
            raise ValueError(f'duplicate model limits: {key}')
        seen.add(key)
    for entry in entries:
        if (entry.provider, entry.model) == (provider, model):
            return ModelLimits(context_window_tokens=entry.context_window_tokens, max_output_tokens=entry.max_output_tokens)
    raise ValueError(f'missing model limits: {provider}/{model}')
