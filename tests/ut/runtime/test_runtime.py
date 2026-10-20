from uuid import uuid4

import pytest

from miniagent.agents import AgentSpec
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.runtime import (
    AgentRuntime,
    InvalidRunTransition,
    RunCursor,
    RunResult,
    RunStatus,
    RuntimeConfig,
    TerminationReason,
    create_run_state,
)
from miniagent.runtime.transitions import (
    begin_evaluation,
    prepare,
    start_running,
)
from miniagent.runtime.transitions import (
    finish as finish_state,
)
from miniagent.tools.registry import ToolRegistry
from tests.support.runtime.runtime import QueueProvider, SuccessfulTool, run_state


class TestRunState:
    def test_transition_returns_new_state_without_mutating_source(self) -> None:
        state = run_state()

        prepared = prepare(state)

        assert prepared is not state
        assert prepared.lifecycle.status is RunStatus.PREPARING
        assert state.lifecycle.status is RunStatus.CREATED
        assert state.lifecycle.transition_count == 0

    def test_valid_lifecycle_reaches_succeeded(self) -> None:
        state = run_state()

        state = start_running(prepare(state))
        state = begin_evaluation(state, "candidate")
        state = finish_state(
            state,
            RunStatus.SUCCEEDED,
            TerminationReason.TASK_VERIFIED,
            final_output="verified",
        )

        assert state.lifecycle.status == RunStatus.SUCCEEDED
        assert state.lifecycle.transition_count == 4
        assert state.lifecycle.started_at is not None
        assert state.lifecycle.finished_at is not None

        result = RunResult.from_state(state)
        assert result.run_id == state.identity.run_id
        assert result.output == "verified"
        assert result.usage == state.usage

    def test_illegal_transition_does_not_mutate_state(self) -> None:
        state = run_state()

        with pytest.raises(InvalidRunTransition):
            start_running(state)

        assert state.lifecycle.status == RunStatus.CREATED
        assert state.lifecycle.transition_count == 0

    def test_cancelled_before_start_can_produce_result(self) -> None:
        state = run_state()
        state = finish_state(
            state,
            RunStatus.CANCELLED,
            TerminationReason.CANCEL_REQUESTED,
        )

        result = RunResult.from_state(state)

        assert result.status == RunStatus.CANCELLED
        assert result.started_at is None

    def test_invalid_target_invariants_do_not_mutate_state(self) -> None:
        state = run_state()
        state = start_running(prepare(state))

        with pytest.raises(InvalidRunTransition):
            start_running(state)

        assert state.lifecycle.status == RunStatus.RUNNING
        assert state.execution.cursor == RunCursor.REQUEST_MODEL
        assert state.lifecycle.transition_count == 2

    def test_reserved_recovery_fields_have_empty_defaults(self) -> None:
        state = run_state()

        assert state.usage.model_requests == 0
        assert state.execution.tool_batch is None
        assert state.approval.pending is None
        assert state.recovery.checkpoint_id is None


def test_request_rejects_fixed_prefix_tampering() -> None:
    from miniagent.models.messages import SystemMessage, UserMessage
    from miniagent.runtime.state import ConversationState

    agent = AgentSpec(
        name="test",
        system_prompt="fixed",
        model=ModelTarget(provider="qwen", model="test"),
        generation=GenerationOptions(max_output_tokens=976),
    )
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=agent,
        config=RuntimeConfig(),
        messages=(SystemMessage(content="fixed"), UserMessage(content="task")),
    )
    runtime = AgentRuntime(provider=QueueProvider([]), tools=ToolRegistry([]))
    assert runtime._build_request(state).generation.max_output_tokens == 976
    altered = state.model_copy(
        update={
            "conversation": ConversationState(
                messages=(
                    SystemMessage(content="tampered"),
                    UserMessage(content="task"),
                )
            )
        }
    )
    with pytest.raises(ValueError, match="fixed"):
        runtime._build_request(altered)


@pytest.mark.parametrize(("change",), [("description",), ("schema",), ("version",)])
def test_request_rejects_changed_tool_contract(change: str) -> None:
    from miniagent.models.messages import SystemMessage, UserMessage
    from miniagent.runtime.state import RunDefinition

    registry = ToolRegistry([SuccessfulTool()])
    agent = AgentSpec(
        name="test",
        system_prompt="fixed",
        model=ModelTarget(provider="qwen", model="test"),
        generation=GenerationOptions(max_output_tokens=976),
        allowed_tools=("ok",),
    )
    state = create_run_state(
        run_id=uuid4(),
        task="task",
        agent_spec=agent,
        config=RuntimeConfig(),
        messages=(SystemMessage(content="fixed"), UserMessage(content="task")),
    )
    definitions = registry.definitions(agent.allowed_tools)
    metadata = [registry.contract_metadata("ok")]
    if change == "version":
        metadata[0]["version"] = "changed"
    else:
        definitions[0] = definitions[0].model_copy(
            update={
                "description" if change == "description" else "input_schema": "changed"
                if change == "description"
                else {"type": "string"}
            }
        )
    state = state.model_copy(
        update={
            "definition": RunDefinition(
                agent_spec=agent,
                config=RuntimeConfig(),
                tool_definitions=tuple(definitions),
                tool_contract_metadata=tuple(metadata),
            )
        }
    )
    with pytest.raises(ValueError, match="tool contract"):
        AgentRuntime(provider=QueueProvider([]), tools=registry)._build_request(state)
