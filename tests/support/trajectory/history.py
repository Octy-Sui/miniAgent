import asyncio
import json
import os
import shlex
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from miniagent.agents import AgentSpec
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.cli.run_definition import (
    freeze_application_definition,
    parse_application_definition,
)
from miniagent.models.config import GenerationOptions, ModelTarget
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelContextLimitError,
    ModelResponse,
    ModelUsage,
)
from miniagent.policy import ApprovalOutcome, ApprovalResolution
from miniagent.runtime import AgentRuntime, InterruptionToken, RunSuspension
from miniagent.runtime.events import NewRunEvent, ProviderIdentityChanged
from miniagent.runtime.state import RunDefinition
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolCall
from tests.support.runtime.context import summary_response


async def prepare_history(
    root: Path, count: int, body_bytes: int, *, soft_summary_failure: bool = False
) -> dict:
    root.mkdir(parents=True)
    source = root / "source"
    source.mkdir()
    await asyncio.to_thread(
        subprocess.run, ["git", "init", "--quiet", str(source)], check=True
    )
    (source / "tracked.txt").write_text("baseline\n")
    limits = root / "limits.toml"
    context_window = 32768 if soft_summary_failure else 5000000
    limits.write_text(
        f'[[models]]\nprovider="qwen"\nmodel="qwen3.8-flash"\ncontext_window_tokens={context_window}\nmax_output_tokens=4096\n'
    )
    os.environ.update(
        OPENAI_API_KEY="resource-fixture",
        OPENAI_BASE_URL="https://provider.test/v1",
        LOCAL_WORKSPACE_ROOT=str(root / "workspaces"),
        ARTIFACT_ROOT=str(root / "artifacts"),
        RUN_DATABASE_PATH=str(root / "runs.db"),
        MODEL_LIMITS_FILE=str(limits),
        MODEL_PRICING_FILE=str(Path.cwd() / "config/model_pricing.toml"),
    )
    agent = AgentSpec(
        name="resource",
        system_prompt=("history input " * (body_bytes // 14 + 1))[:body_bytes],
        model=ModelTarget(provider="qwen", model="qwen3.8-flash"),
        generation=GenerationOptions(max_output_tokens=100),
        allowed_tools=("shell",),
    )
    app = await bootstrap(BootstrapConfig(agent=agent, task="task", source_dir=source))
    deps = app.dependencies
    config = deps.config.model_copy(
        update={"max_steps": 3000, "recent_steps_to_keep": 1}
    )
    definition = RunDefinition(
        agent_spec=agent,
        config=config,
        tool_definitions=tuple(deps.tools.definitions(agent.allowed_tools)),
        tool_contract_metadata=tuple(
            deps.tools.contract_metadata(name) for name in agent.allowed_tools
        ),
    )
    assert deps.recovery_context.application is not None
    assert isinstance(deps.run_store, SQLiteRunStore)
    frozen = parse_application_definition(
        deps.recovery_context.application,
        RunDefinition(
            agent_spec=deps.agent,
            config=deps.config,
            tool_definitions=definition.tool_definitions,
            tool_contract_metadata=definition.tool_contract_metadata,
        ),
    )
    if soft_summary_failure:
        frozen = frozen.model_copy(update={"export_trajectory": True})
    context = deps.recovery_context.model_copy(
        update={"application": freeze_application_definition(frozen, definition)}
    )
    token = InterruptionToken()
    steps = 0 if count < 32 else 1 if count < 128 else max(6, (count - 40) // 16)
    usage = ModelUsage(input_tokens=10, output_tokens=5, total_tokens=15)

    class Provider:
        def __init__(self):
            self.completed = 0
            self.capacity = set()
            self.summary_calls = 0

        async def generate(self, request):
            if "summary_schema" in (request.messages[-1].content or ""):
                self.summary_calls += 1
                if soft_summary_failure and self.summary_calls == 1:
                    return ModelResponse(
                        message=AssistantMessage(content="invalid summary body"),
                        finish_reason=FinishReason.STOP,
                        usage=usage,
                    )
                return summary_response().model_copy(update={"usage": usage})
            if (
                not soft_summary_failure
                and count >= 128
                and self.completed in (6, 12)
                and self.completed not in self.capacity
            ):
                self.capacity.add(self.completed)
                raise ModelContextLimitError("external provider capacity")
            if self.completed >= steps:
                token.request(2)
                await asyncio.Event().wait()
            self.completed += 1
            program = (
                "import sys;sys.stdout.write('z'*8192)"
                if self.completed <= 13
                else "print('done')"
            )
            command = shlex.quote(sys.executable) + " -c " + shlex.quote(program)
            return ModelResponse(
                message=AssistantMessage(
                    tool_calls=(
                        ToolCall(
                            id=f"call-{self.completed}",
                            name="shell",
                            arguments={"command": command},
                        ),
                    )
                ),
                finish_reason=FinishReason.TOOL_CALL,
                usage=usage,
            )

    class Human:
        async def resolve(self, request, decision):
            return ApprovalResolution(
                approval_id=request.approval_id,
                outcome=ApprovalOutcome.APPROVED,
                actor_kind="human",
                actor_id="resource-fixture",
                reason="owned command",
                resolved_at=datetime.now(UTC),
            )

    provider = Provider()
    result = await AgentRuntime(
        provider=provider,
        pricing=deps.pricing,
        tools=deps.tools,
        config=config,
        run_store=deps.run_store,
        artifact_store=deps.artifact_store,
        approval_handler=Human(),
        policy_context=deps.policy_context,
    ).run(
        agent,
        "task",
        run_id=deps.run_id,
        recovery_context=context,
        interruption_token=token,
    )
    assert isinstance(result, RunSuspension)
    record = await deps.run_store.get(deps.run_id)
    actual = record.last_event_sequence
    if actual > count:
        raise AssertionError(f"History overhead {actual} exceeds requested {count}")
    while record.last_event_sequence < count:
        await deps.run_store.append(
            NewRunEvent(
                run_id=deps.run_id,
                payload=ProviderIdentityChanged(
                    original_endpoint_identity="https://provider.test/v1",
                    current_endpoint_identity="https://provider.test/v1",
                ),
            ),
            expected_sequence=record.last_event_sequence,
        )
        record = await deps.run_store.get(deps.run_id)
    metadata = {
        "run_id": str(deps.run_id),
        "count": count,
        "body_bytes": body_bytes,
        "steps": provider.completed,
        "unpadded_events": actual,
        "context_window_tokens": config.context_window_tokens,
        "capacity_responses": sorted(provider.capacity),
        "summary_calls": provider.summary_calls,
        "soft_summary_failure": soft_summary_failure,
    }
    (root / "history.json").write_text(json.dumps(metadata))
    return metadata


if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(
                prepare_history(
                    Path(sys.argv[1]),
                    int(sys.argv[2]),
                    int(sys.argv[3]),
                    soft_summary_failure="--soft-summary-failure" in sys.argv[4:],
                )
            )
        )
    )
