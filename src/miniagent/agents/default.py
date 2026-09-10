from miniagent.agents.spec import AgentSpec, ModelTarget

SYSTEM_PROMPT = (
    "You are a helpful assistant that can interact with a computer shell "
    "to solve programming tasks.\n"
)

DEFAULT_AGENT = AgentSpec(
    name="default",
    system_prompt=SYSTEM_PROMPT,
    model=ModelTarget(provider="qwen", model="qwen3.8-flash"),
    allowed_tools=["read_file", "shell"],
    temperature=0,
    max_output_tokens=1_000_000,
)
