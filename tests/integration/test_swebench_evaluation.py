import os
from dataclasses import replace
from pathlib import Path

import pytest

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.application import Application
from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.environments import Command, ExecutionStatus, WorkspaceKind
from miniagent.evaluation.swebench_data import SWEbenchTaskContext, load_support_list
from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import FinishReason, ModelResponse, ModelUsage
from miniagent.runtime import (
    CancellationToken,
    RunEventType,
    RunStatus,
    TerminationReason,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("MINIAGENT_REAL_SWEBENCH") != "1",
    reason="Real SWE-bench requires explicit cached-image opt-in",
)


class SubmissionProvider:
    async def generate(self, request):
        assert "gold" not in request.model_dump_json().lower()
        return ModelResponse(
            message=AssistantMessage(content="Candidate submitted"),
            finish_reason=FinishReason.STOP,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


async def real_application():
    instance = os.environ["MINIAGENT_SWEBENCH_INSTANCE_ID"]
    support = next(
        task for task in load_support_list()["tasks"] if task["instance_id"] == instance
    )
    assert support["selection"] == "development"
    return await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="Validate this development candidate",
            source_dir=Path.cwd(),
            workspace_kind=WorkspaceKind.DOCKER,
            docker_image=support["image"],
            swebench_task=SWEbenchTaskContext(instance_id=instance),
        )
    )


async def verify_application(application, passed):
    deps = application.dependencies
    application = Application(replace(deps, provider=SubmissionProvider()))
    result = await application.run(CancellationToken())
    assert result.run_result.status is (
        RunStatus.SUCCEEDED if passed else RunStatus.FAILED
    ), result.run_result.error
    assert result.run_result.reason is (
        TerminationReason.TASK_VERIFIED
        if passed
        else TerminationReason.EVALUATION_FAILED
    )
    assert not result.finalization_errors
    assert {
        "workspace_patch",
        "evaluation_report",
        "evaluation_predictions",
        "evaluation_summary",
    } <= {ref.type for ref in result.artifacts}
    events = await deps.run_store.list_events(deps.run_id)
    completed = next(
        event for event in events if event.type is RunEventType.EVALUATION_COMPLETED
    )
    assert {ref.artifact_id for ref in completed.artifacts} == {
        ref.artifact_id for ref in result.artifacts
    }
    print(
        f"real run_id={deps.run_id} status={result.run_result.status.value} artifacts={len(result.artifacts)}"
    )
    if not passed:
        await deps.workspace.destroy()


async def test_real_ready_development_patch_is_verified():
    application = await real_application()
    patch = Path(os.environ["MINIAGENT_SWEBENCH_REFERENCE_PATCH"]).read_text(
        encoding="utf-8"
    )
    applied = await application.dependencies.workspace.execute(
        Command(argv=["git", "apply", "--binary", "-"], stdin=patch)
    )
    assert applied.status is ExecutionStatus.SUCCESS
    await verify_application(application, True)


async def test_real_invalid_patch_fails():
    application = await real_application()
    modified = await application.dependencies.workspace.execute(
        Command(
            argv=[
                "/bin/sh",
                "-c",
                "printf '\n# unchanged behavior candidate\n' >> django/urls/resolvers.py",
            ]
        )
    )
    assert modified.status is ExecutionStatus.SUCCESS
    await verify_application(application, False)
