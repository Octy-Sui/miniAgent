from miniagent.agents.spec import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget

DEFAULT_MODEL = ModelTarget(provider="qwen", model="qwen3.8-flash")


def build_default_agent(system_prompt: str) -> AgentSpec:
    return AgentSpec(
        name="default",
        system_prompt=system_prompt,
        model=DEFAULT_MODEL,
        generation=GenerationOptions(temperature=0, max_output_tokens=4096),
        allowed_tools=("shell",),
    )
