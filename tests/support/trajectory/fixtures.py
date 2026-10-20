from miniagent.models.protocol import ModelRequest
from miniagent.runtime.state import RunState


def request_for(state: RunState) -> ModelRequest:
    return ModelRequest(
        model=state.definition.agent_spec.model.model,
        messages=state.conversation.messages,
        tools=state.definition.tool_definitions,
        generation=state.definition.agent_spec.generation,
    )
