import pytest

from miniagent.models.messages import (
    AssistantMessage,
    ToolMessage,
)
from miniagent.runtime.context import (
    ContextBudget,
    ContextCapacityError,
    QwenPromptTokenEstimator,
    select_compaction_prefix,
    should_compact,
)
from miniagent.runtime.state import ConversationState, StepRange
from miniagent.tools.contracts import ToolCall, ToolDefinition
from tests.support.runtime.context import (
    SizedEstimator,
    long_state,
    request_for,
    summary_plan,
    summary_response,
)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (6999, False),
        (7000, True),
        (7001, True),
        (9999, True),
        (10000, True),
        (10001, True),
    ],
)
def test_trigger_boundaries(tokens: int, expected: bool) -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(tokens))
    assert (plan is not None) is expected
    if plan:
        assert plan.hard_trigger is (tokens > 10000)


def test_complete_tool_groups_with_reused_call_ids() -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert plan is not None
    assert plan.covered_steps == (1, 2, 3)
    assert plan.retained_first_step == 4
    assert plan.covered_messages == (2, 9)
    assert state.conversation.messages[9:] == tuple(
        message
        for step in state.conversation.completed_steps[3:]
        for message in state.conversation.messages[
            step.message_start : step.message_end
        ]
    )


@pytest.mark.parametrize(
    ("remaining", "cost", "expected"), [(1, 1, False), (3, 30000, False), (3, 1, True)]
)
def test_soft_cost_filter(remaining: int, cost: int, expected: bool) -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(7000))
    assert plan is not None
    assert (
        should_compact(plan, summary_request_tokens=cost, remaining_steps=remaining)
        is expected
    )


def test_hard_trigger_ignores_cost() -> None:
    state = long_state()
    plan = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert plan is not None
    assert should_compact(plan, summary_request_tokens=999999, remaining_steps=1)


def test_fixed_prefix_or_retained_suffix_over_budget() -> None:
    state = long_state()
    with pytest.raises(ContextCapacityError, match="retained"):
        select_compaction_prefix(
            state, request_for(state), SizedEstimator(10001, 10001)
        )


def test_budget_and_schema_overhead() -> None:
    state = long_state()
    budget = ContextBudget.for_state(state)
    assert (budget.C, budget.O, budget.S, budget.B, budget.summary_reserve) == (
        12000,
        976,
        1024,
        10000,
        1500,
    )
    estimator = QwenPromptTokenEstimator()
    request = request_for(state)
    with_tools = request.model_copy(
        update={
            "tools": (
                ToolDefinition(
                    name="ok",
                    description="long schema " * 100,
                    input_schema={"type": "object"},
                ),
            )
        }
    )
    assert estimator.estimate(with_tools) > estimator.estimate(request)


def test_summary_request_has_fixed_identity_and_no_tools() -> None:
    from miniagent.runtime.context import build_summary_request

    plan = summary_plan()
    request = build_summary_request(plan.state, plan)
    assert request.messages[:2] == plan.request.messages[:2]
    assert request.tools == ()
    assert request.generation.max_output_tokens == 1500
    assert "message_start" in (request.messages[-1].content or "")
    assert "done step 1" in (request.messages[-1].content or "")
    assert "done step 4" not in (request.messages[-1].content or "")


@pytest.mark.parametrize(
    ("changes",),
    [
        ({"current_goal": ""},),
        ({"next_action": "unrelated"},),
        ({"unexpected": "field"},),
        ({"open_items": []},),
    ],
)
async def test_invalid_summary_contract(changes) -> None:
    from miniagent.runtime.context import InvalidSummaryError, validate_summary

    with pytest.raises(InvalidSummaryError):
        await validate_summary(summary_response(**changes), summary_plan(), None)


async def test_missing_summary_field_and_truncation_rejected() -> None:
    import json

    from miniagent.models.protocol import FinishReason
    from miniagent.runtime.context import InvalidSummaryError, validate_summary

    response = summary_response()
    data = json.loads(response.message.content)
    del data["uncertainties"]
    with pytest.raises(InvalidSummaryError):
        await validate_summary(
            response.model_copy(
                update={"message": AssistantMessage(content=json.dumps(data))}
            ),
            summary_plan(),
            None,
        )
    for altered in [
        response.model_copy(update={"finish_reason": FinishReason.LENGTH}),
        response.model_copy(
            update={
                "message": AssistantMessage(
                    content=response.message.content,
                    tool_calls=(ToolCall(id="bad", name="ok", arguments={}),),
                )
            }
        ),
    ]:
        with pytest.raises(InvalidSummaryError):
            await validate_summary(altered, summary_plan(), None)


async def test_valid_summary_is_one_assistant_and_preserves_recent_steps() -> None:
    from miniagent.runtime.context import validate_summary

    plan = summary_plan()
    candidate = await validate_summary(summary_response(), plan, None)
    assert candidate.messages[:2] == plan.request.messages[:2]
    assert isinstance(candidate.messages[2], AssistantMessage)
    assert candidate.messages[3:] == plan.request.messages[9:]
    assert [step.step_number for step in candidate.completed_steps] == [4, 5, 6]
    assert candidate.completed_steps[0].message_start == 3


async def test_without_store_removed_source_cannot_be_verified() -> None:
    from miniagent.runtime.context import InvalidSummaryError, validate_summary

    plan = summary_plan()
    evidence = {
        "claim": "done step 1",
        "excerpt": "done step 1",
        "source": {
            "kind": "snapshot",
            "run_id": str(plan.state.identity.run_id),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 4,
        },
    }
    with pytest.raises(InvalidSummaryError, match="source"):
        await validate_summary(
            summary_response(verified_evidence=[evidence]), plan, None
        )


async def test_later_summary_request_contains_previous_summary() -> None:
    from miniagent.runtime.context import build_summary_request, validate_summary

    first = summary_plan()
    conversation = await validate_summary(summary_response(), first, None)
    messages = list(conversation.messages)
    ranges = list(conversation.completed_steps)
    for number in range(7, 10):
        start = len(messages)
        messages.extend(
            (
                AssistantMessage(
                    tool_calls=(ToolCall(id="reused", name="ok", arguments={}),)
                ),
                ToolMessage(tool_call_id="reused", content=f"done step {number}"),
            )
        )
        ranges.append(
            StepRange(
                step_number=number, message_start=start, message_end=len(messages)
            )
        )
    state = first.state.model_copy(
        update={
            "conversation": ConversationState(
                messages=tuple(messages), completed_steps=tuple(ranges)
            )
        }
    )
    second = select_compaction_prefix(state, request_for(state), SizedEstimator(10001))
    assert second is not None
    request = build_summary_request(state, second)
    assert request.messages[:2] == first.request.messages[:2]
    assert "Historical continuation summary" in (request.messages[-1].content or "")
    assert "done step 4" in (request.messages[-1].content or "")
    assert request.tools == ()


async def test_summary_rejects_whitespace_only_next_action() -> None:
    from miniagent.runtime.context import InvalidSummaryError, validate_summary

    with pytest.raises(InvalidSummaryError):
        await validate_summary(
            summary_response(open_items=["   "], next_action="   "),
            summary_plan(),
            None,
        )
