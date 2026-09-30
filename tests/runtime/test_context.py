from uuid import uuid4

import pytest

from miniagent.agents import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.context import ContextBudget, ContextCapacityError, QwenPromptTokenEstimator, select_compaction_prefix, should_compact
from miniagent.runtime.state import ConversationState, StepRange, create_run_state
from miniagent.runtime.transitions import prepare, start_running
from miniagent.tools.contracts import ToolCall, ToolDefinition


class SizedEstimator:
    def __init__(self, total: int, retained: int = 1000) -> None:
        self.total = total
        self.retained = retained

    def estimate(self, request: ModelRequest) -> int:
        return self.total if len(request.messages) >= 10 else self.retained


def long_state():
    agent = AgentSpec(name='test', system_prompt='fixed', model=ModelTarget(provider='qwen', model='test'), generation=GenerationOptions(max_output_tokens=976))
    state = start_running(prepare(create_run_state(run_id=uuid4(), task='task', agent_spec=agent, config=RuntimeConfig(context_window_tokens=12000), messages=(SystemMessage(content='fixed'), UserMessage(content='task')))))
    messages = list(state.conversation.messages)
    ranges = []
    for step in range(1, 7):
        start = len(messages)
        calls = (ToolCall(id='reused', name='ok', arguments={}),)
        if step == 2:
            calls += (ToolCall(id='second', name='ok', arguments={}),)
        messages.append(AssistantMessage(tool_calls=calls))
        messages.extend(ToolMessage(tool_call_id=call.id, content=f'done step {step}') for call in calls)
        ranges.append(StepRange(step_number=step, message_start=start, message_end=len(messages)))
    return state.model_copy(update={'conversation': ConversationState(messages=tuple(messages), completed_steps=tuple(ranges)), 'execution': state.execution.model_copy(update={'steps_started': 6})})


def request_for(state):
    return ModelRequest(model='test', messages=state.conversation.messages, generation=state.definition.agent_spec.generation)


@pytest.mark.parametrize(('tokens', 'expected'), [(6999, False), (7000, True), (7001, True), (9999, True), (10000, True), (10001, True)])
def test_trigger_boundaries(tokens: int, expected: bool) -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(tokens))
    assert (plan is not None) is expected
    if plan:
        assert plan.hard_trigger is (tokens > 10000)


def test_complete_tool_groups_with_reused_call_ids() -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert plan.covered_steps == (1, 2, 3)
    assert plan.retained_first_step == 4
    assert plan.covered_messages == (2, 9)
    assert state.conversation.messages[9:] == tuple(message for step in state.conversation.completed_steps[3:] for message in state.conversation.messages[step.message_start:step.message_end])


@pytest.mark.parametrize(('remaining', 'cost', 'expected'), [(1, 1, False), (3, 30000, False), (3, 1, True)])
def test_soft_cost_filter(remaining: int, cost: int, expected: bool) -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(7000))
    assert should_compact(plan, summary_request_tokens=cost, remaining_steps=remaining) is expected


def test_hard_trigger_ignores_cost() -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert should_compact(plan, summary_request_tokens=999999, remaining_steps=1)


def test_fixed_prefix_or_retained_suffix_over_budget() -> None:
    state = long_state()
    with pytest.raises(ContextCapacityError, match='retained'):
        select_compaction_prefix(state, request_for(state), SizedEstimator(10001, 10001))


def test_budget_and_schema_overhead() -> None:
    state = long_state()
    budget = ContextBudget.for_state(state)
    assert (budget.C, budget.O, budget.S, budget.B, budget.summary_reserve) == (12000, 976, 1024, 10000, 1500)
    estimator = QwenPromptTokenEstimator()
    request = request_for(state)
    with_tools = request.model_copy(update={'tools': (ToolDefinition(name='ok', description='long schema ' * 100, input_schema={'type': 'object'}),)})
    assert estimator.estimate(with_tools) > estimator.estimate(request)
