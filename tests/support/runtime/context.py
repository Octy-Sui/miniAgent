from uuid import uuid4

from miniagent.agents import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.context import (
    select_compaction_prefix,
)
from miniagent.runtime.state import ConversationState, StepRange, create_run_state
from miniagent.runtime.transitions import prepare, start_running
from miniagent.tools.contracts import ToolCall


class SizedEstimator:
    def __init__(self, total: int, retained: int = 1000) -> None:
        self.total = total
        self.retained = retained

    def estimate(self, request: ModelRequest) -> int:
        return self.total if len(request.messages) >= 10 else self.retained


def long_state():
    agent = AgentSpec(
        name="test",
        system_prompt="fixed",
        model=ModelTarget(provider="qwen", model="test"),
        generation=GenerationOptions(max_output_tokens=976),
    )
    state = start_running(
        prepare(
            create_run_state(
                run_id=uuid4(),
                task="task",
                agent_spec=agent,
                config=RuntimeConfig(context_window_tokens=12000),
                messages=(SystemMessage(content="fixed"), UserMessage(content="task")),
            )
        )
    )
    messages = list(state.conversation.messages)
    ranges = []
    for step in range(1, 7):
        start = len(messages)
        calls = (ToolCall(id="reused", name="ok", arguments={}),)
        if step == 2:
            calls += (ToolCall(id="second", name="ok", arguments={}),)
        messages.append(AssistantMessage(tool_calls=calls))
        messages.extend(
            ToolMessage(tool_call_id=call.id, content=f"done step {step}")
            for call in calls
        )
        ranges.append(
            StepRange(step_number=step, message_start=start, message_end=len(messages))
        )
    return state.model_copy(
        update={
            "conversation": ConversationState(
                messages=tuple(messages), completed_steps=tuple(ranges)
            ),
            "execution": state.execution.model_copy(update={"steps_started": 6}),
        }
    )


def request_for(state):
    return ModelRequest(
        model="test",
        messages=state.conversation.messages,
        generation=state.definition.agent_spec.generation,
    )


def summary_response(**changes):
    import json

    from miniagent.models.protocol import FinishReason, ModelResponse

    data = {
        "current_goal": "task",
        "user_constraints": ["fixed"],
        "verified_evidence": [],
        "workspace_changes": [],
        "rejected_attempts": [],
        "uncertainties": [],
        "open_items": ["finish task"],
        "next_action": "finish task",
    }
    data.update(changes)
    return ModelResponse(
        message=AssistantMessage(content=json.dumps(data)),
        finish_reason=FinishReason.STOP,
    )


def summary_plan():
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert plan is not None
    return plan
