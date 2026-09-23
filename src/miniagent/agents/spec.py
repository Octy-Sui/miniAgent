# File   : spec.py
# Created: 2026-09-11 00:00:25
# Author : octy Sui

from pydantic import BaseModel, ConfigDict

from miniagent.models.config import GenerationOptions, ModelTarget


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    system_prompt: str
    model: ModelTarget
    generation: GenerationOptions = GenerationOptions()
    allowed_tools: tuple[str, ...] = ()
