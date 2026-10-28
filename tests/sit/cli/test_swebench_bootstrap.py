import json

import pytest
from typer.testing import CliRunner

from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.cli.candidate_submission import WorkspaceCandidateEvaluator
from miniagent.cli.main import app
from miniagent.environments import WorkspaceKind
from miniagent.evaluation.swebench import SWEbenchEvaluator
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
)
from tests.support.cli.agent import DEFAULT_AGENT


async def test_docker_context_required_before_workspace_creation(configured_host):
    with pytest.raises(EvaluationPreparationError, match="TASK_CONTEXT_REQUIRED"):
        await bootstrap(
            BootstrapConfig(
                agent=DEFAULT_AGENT,
                task="issue django__django-11477",
                source_dir=configured_host,
                workspace_kind=WorkspaceKind.DOCKER,
                docker_image="arbitrary-agent-image",
            )
        )
    assert not (configured_host / "docker-calls.jsonl").exists()
    assert not (configured_host / "journal.sqlite3").exists()


@pytest.mark.parametrize(
    ("instance", "code"),
    [("other", "TASK_UNSUPPORTED"), ("astropy__astropy-12907", "TASK_NOT_READY")],
)
async def test_unready_or_unsupported_task_never_creates_workspace(
    configured_host, instance, code
):
    with pytest.raises(EvaluationPreparationError, match=code):
        await bootstrap(
            BootstrapConfig(
                agent=DEFAULT_AGENT,
                task="issue",
                source_dir=configured_host,
                workspace_kind=WorkspaceKind.DOCKER,
                docker_image="agent",
                swebench_task=SWEbenchTaskContext(instance_id=instance),
            )
        )
    assert not (configured_host / "docker-calls.jsonl").exists()


async def test_ready_task_bootstraps_with_global_ready_false_and_override(
    configured_host,
):
    application = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="issue for other ID",
            source_dir=configured_host,
            workspace_kind=WorkspaceKind.DOCKER,
            docker_image="arbitrary-agent-image",
            swebench_task=SWEbenchTaskContext(
                instance_id="django__django-11477", evaluation_image="custom:local"
            ),
        )
    )
    assert isinstance(application.dependencies.evaluator, WorkspaceCandidateEvaluator)
    evaluator = application.dependencies.evaluator.evaluator
    assert isinstance(evaluator, SWEbenchEvaluator)
    assert evaluator.prepared.record.instance_id == "django__django-11477"
    assert evaluator.prepared.actual_image == "sha256:synthetic"
    assert evaluator.prepared.image_preflight_verified is False
    await application.dependencies.workspace.destroy()


async def test_missing_cached_image_rejects_before_agent_creation(configured_host):
    path = configured_host / "docker-scenario.json"
    scenario = json.loads(path.read_bytes())
    scenario["image_missing"] = True
    path.write_text(json.dumps(scenario))
    with pytest.raises(
        EvaluationPreparationError, match="EVALUATION_IMAGE_UNAVAILABLE"
    ):
        await bootstrap(
            BootstrapConfig(
                agent=DEFAULT_AGENT,
                task="issue",
                source_dir=configured_host,
                workspace_kind=WorkspaceKind.DOCKER,
                docker_image="agent",
                swebench_task=SWEbenchTaskContext(instance_id="django__django-11477"),
            )
        )
    calls = [
        json.loads(line)["argv"]
        for line in (configured_host / "docker-calls.jsonl").read_text().splitlines()
    ]
    assert not any("create" in call for call in calls)


def test_plain_docker_cli_reports_known_preparation_error(configured_host):
    result = CliRunner().invoke(
        app, ["task", "--workspace", "docker", "--docker-image", "agent"]
    )
    assert result.exit_code == 1
    assert "TASK_CONTEXT_REQUIRED" in result.stderr


async def test_local_ignores_missing_harness_and_data(
    configured_host, submission_case, monkeypatch
):
    workspace, _ = submission_case
    monkeypatch.setenv("SWEBENCH_DATA_ROOT", str(configured_host / "absent-data"))
    monkeypatch.setenv("SWEBENCH_EXECUTABLE", "/absent/harness")
    application = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="issue",
            source_dir=workspace.spec.options.source_dir,
            workspace_kind=WorkspaceKind.LOCAL,
        )
    )
    assert not isinstance(
        application.dependencies.evaluator, WorkspaceCandidateEvaluator
    )
    assert not (configured_host / "docker-calls.jsonl").exists()
    await application.dependencies.workspace.destroy()


async def test_wrong_harness_version_is_rejected_before_docker(configured_host):
    executable = configured_host / "harness-test"
    executable.write_text(
        executable.read_text().replace("swebench 5.0.2", "swebench 5.0.3")
    )
    with pytest.raises(EvaluationPreparationError, match="HARNESS_VERSION_MISMATCH"):
        await bootstrap(
            BootstrapConfig(
                agent=DEFAULT_AGENT,
                task="issue",
                source_dir=configured_host,
                workspace_kind=WorkspaceKind.DOCKER,
                docker_image="agent",
                swebench_task=SWEbenchTaskContext(instance_id="django__django-11477"),
            )
        )
    assert not (configured_host / "docker-calls.jsonl").exists()


async def test_multicall_docker_symlink_keeps_invocation_name(
    configured_host, monkeypatch
):
    executable = configured_host / "docker-test"
    text = executable.read_text()
    text = text.replace(
        "root = Path(__file__).parent",
        'if Path(sys.argv[0]).name not in {"docker-selection", "docker"}:\n    sys.exit(127)\nroot = Path(__file__).parent',
    )
    executable.write_text(text)
    selected = configured_host / "docker-selection"
    selected.symlink_to(executable)
    monkeypatch.setenv("EVALUATION_DOCKER_EXECUTABLE", str(selected))
    application = await bootstrap(
        BootstrapConfig(
            agent=DEFAULT_AGENT,
            task="issue",
            source_dir=configured_host,
            workspace_kind=WorkspaceKind.DOCKER,
            docker_image="agent",
            swebench_task=SWEbenchTaskContext(instance_id="django__django-11477"),
        )
    )
    await application.dependencies.workspace.destroy()
