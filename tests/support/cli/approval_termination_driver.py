import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from miniagent.cli.signals import run_with_signals
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.policy import ApprovalOutcome, ApprovalResolution, DefaultPolicyEngine
from miniagent.runtime import RuntimeConfig, create_run_state
from miniagent.runtime.approval import ApprovalCoordinator
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.progress import TerminationDecision, ToolsDisposition
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from tests.support.runtime.runtime import agent
from tests.support.storage.sqlite import make_decision
from tests.support.trajectory.fixtures import request_for


async def run(root: Path, mode: str) -> int:
    async def operation(cancellation, interruption):
        store = SQLiteRunStore(root / "runs.sqlite3")
        await store.initialize()
        state = create_run_state(
            run_id=uuid4(), task="task", agent_spec=agent(), config=RuntimeConfig()
        )
        execution = await RunExecution.start(
            state,
            RunJournal(
                RunRecorder(
                    run_id=state.identity.run_id, store=store, recovery_context=None
                )
            ),
        )
        await execution.begin_step(
            provider="test", model="test-model", request=request_for(execution.state)
        )
        call = ToolCall(id="call-1", name="shell", arguments={"command": "pytest"})
        await execution.record_model_response(
            ModelResponse(
                message=AssistantMessage(tool_calls=(call,)),
                finish_reason=FinishReason.TOOL_CALL,
            ),
            None,
            disposition=ToolsDisposition(),
        )
        await execution.begin_tool(
            call_index=0, tool_call_id=call.id, tool_name=call.name
        )

        class Handler:
            async def resolve(self, request, decision):
                if mode == "handler_error":
                    raise ValueError("external approval UI failed")
                return ApprovalResolution(
                    approval_id=request.approval_id,
                    outcome=ApprovalOutcome.REJECTED
                    if mode == "rejected"
                    else ApprovalOutcome.EXPIRED,
                    actor_kind="human" if mode == "rejected" else "system",
                    actor_id="acceptance",
                    reason="explicit decision",
                    resolved_at=datetime.now(UTC),
                )

        outcome = await ApprovalCoordinator(
            config=state.definition.config,
            handler=Handler(),
            policy_engine=DefaultPolicyEngine(),
            cancellation_token=cancellation,
        ).authorize(execution, make_decision(execution.state))
        assert outcome.termination is not None
        termination = outcome.termination
        await execution.record_termination(
            TerminationDecision(
                status="failed", reason=termination.reason, error=termination.error
            )
        )
        print("ready", flush=True)
        await interruption.wait()
        return await execution.suspend(
            interruption.interruption, deadline=interruption.deadline
        )

    _, received = await run_with_signals(operation)
    assert received is not None
    return 128 + received.value


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(Path(sys.argv[1]), sys.argv[2])))
