# File   : default_agent.py
# Created: 2026-09-11 00:00:22
# Author : octy Sui

from miniagent.agents.spec import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget

SYSTEM_PROMPT = (
    "You are a helpful assistant that can interact with a computer shell "
    "to solve programming tasks.\n"
)

DEFAULT_AGENT = AgentSpec(
    name="default",
    system_prompt=SYSTEM_PROMPT,
    model=ModelTarget(provider="qwen", model="qwen3.8-flash"),
    generation=GenerationOptions(temperature=0, max_output_tokens=4096),
    allowed_tools=("shell",),
)
