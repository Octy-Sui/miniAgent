from dataclasses import dataclass
from math import ceil
from typing import Protocol

import orjson

from miniagent.models.messages import AssistantMessage, ToolMessage
from miniagent.models.protocol import ModelRequest
from miniagent.runtime.state import RunCursor, RunState, RunStatus


class ContextCapacityError(ValueError):
    pass


class PromptTokenEstimator(Protocol):
    def estimate(self, request: ModelRequest) -> int: ...


class QwenPromptTokenEstimator:
    """Approximate serialized UTF-8 request size; never a provider capacity guarantee."""

    def __init__(self) -> None:
        self._calibration = 1.0

    def estimate(self, request: ModelRequest) -> int:
        size = len(orjson.dumps({'messages': [m.model_dump(mode='json') for m in request.messages], 'tools': [t.model_dump(mode='json') for t in request.tools]}))
        return ceil((ceil(size / 3) + 8 * len(request.messages) + 16) * self._calibration)

    def observe(self, request: ModelRequest, input_tokens: int) -> None:
        if input_tokens > 0:
            estimate = self.estimate(request)
            self._calibration *= max(1.0, input_tokens / estimate)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    C: int
    O: int
    S: int
    B: int
    summary_reserve: int

    @classmethod
    def for_state(cls, state: RunState) -> 'ContextBudget':
        config = state.definition.config
        output = state.definition.agent_spec.generation.max_output_tokens
        config.validate_output_limit(output)
        assert output is not None
        capacity = config.context_window_tokens
        margin = max(1024, ceil(.05 * capacity))
        budget = capacity - output - margin
        return cls(capacity, output, margin, budget, min(2048, int(.15 * budget)))


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    state: RunState
    request: ModelRequest
    budget: ContextBudget
    covered_messages: tuple[int, int]
    covered_steps: tuple[int, ...]
    retained_first_step: int
    T: int
    D: int
    hard_trigger: bool


def select_compaction_prefix(state: RunState, request: ModelRequest, estimator: PromptTokenEstimator, *, force: bool = False) -> CompactionPlan | None:
    budget = ContextBudget.for_state(state)
    tokens = estimator.estimate(request)
    hard = tokens > budget.B or force
    if state.lifecycle.status is not RunStatus.RUNNING or state.execution.cursor is not RunCursor.REQUEST_MODEL or state.execution.tool_batch is not None or state.approval.pending is not None:
        if hard:
            raise ContextCapacityError('no safe completed-Step boundary')
        return None
    if not hard and tokens < state.definition.config.compaction_trigger_ratio * budget.B:
        return None
    steps = state.conversation.completed_steps
    keep = state.definition.config.recent_steps_to_keep
    if len(steps) <= keep:
        if hard:
            raise ContextCapacityError('no compressible complete old Step')
        return None
    # Check locally within each recorded Step: IDs may repeat between Steps.
    expected_start = 3 if state.conversation.messages[2:3] and not steps[0].message_start == 2 else 2
    for step in steps:
        if step.message_start != expected_start or step.message_end > len(request.messages):
            raise ContextCapacityError('invalid completed-Step range')
        group = request.messages[step.message_start:step.message_end]
        first = group[0]
        if not isinstance(first, AssistantMessage) or not first.tool_calls or len(group) != len(first.tool_calls) + 1:
            raise ContextCapacityError('incomplete tool group')
        if any(not isinstance(message, ToolMessage) or message.tool_call_id != call.id for message, call in zip(group[1:], first.tool_calls, strict=True)):
            raise ContextCapacityError('incomplete tool group')
        expected_start = step.message_end
    target = budget.B if hard else state.definition.config.compaction_target_ratio * budget.B
    selected = None
    for count in range(1, len(steps) - keep + 1):
        end = steps[count - 1].message_end
        retained = request.model_copy(update={'messages': (*request.messages[:2], *request.messages[end:])})
        retained_tokens = estimator.estimate(retained)
        selected = (count, end, retained_tokens)
        if retained_tokens + budget.summary_reserve <= target:
            break
    assert selected is not None
    count, end, retained_tokens = selected
    if retained_tokens > budget.B:
        raise ContextCapacityError('fixed prefix or retained recent Steps exceed budget')
    return CompactionPlan(state, request, budget, (2, end), tuple(step.step_number for step in steps[:count]), steps[count].step_number, tokens, tokens - retained_tokens - budget.summary_reserve, hard)


def should_compact(plan: CompactionPlan, *, summary_request_tokens: int, remaining_steps: int) -> bool:
    if plan.hard_trigger:
        return True
    horizon = min(3, remaining_steps)
    return horizon >= 2 and plan.D > 0 and horizon * plan.D > summary_request_tokens + plan.budget.summary_reserve
