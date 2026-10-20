from uuid import uuid4

import pytest
from typer.testing import CliRunner

from miniagent.artifacts import ArtifactRef
from miniagent.cli import main as cli_main
from miniagent.cli.application import (
    FinalizationError,
)
from miniagent.cli.bootstrap import BootstrapConfig
from miniagent.cli.main import (
    app,
)
from tests.support.cli.main import stub_cli_run, successful_result


def test_successful_run_prints_cost_after_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_cli_run(monkeypatch, successful_result())

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert invocation.stdout == (
        "answer\n"
        "Token usage: total=175663 input=132935 (+93920 cached) "
        "output=42728 (14382 reasoning)\n"
        "Cost: total=$0.10444760 input=$0.05317400 "
        "output=$0.05127360 USD\n"
    )


def test_separate_cache_pricing_prints_cache_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_cli_run(monkeypatch, successful_result(separate_cache_pricing=True))

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert (
        "Cost: total=$0.07627160 uncached_input=$0.01560600 "
        "cached=$0.00939200 output=$0.05127360 USD\n" in invocation.stdout
    )


def test_successful_run_prints_persisted_patch_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_cli_run(
        monkeypatch,
        successful_result(),
        patch_artifact=ArtifactRef(
            artifact_id=uuid4(),
            uri="file:///artifacts/changes.patch",
            type="workspace_patch",
        ),
    )

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert "Patch: file:///artifacts/changes.patch\n" in invocation.stdout


def test_successful_run_with_finalization_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_cli_run(
        monkeypatch,
        successful_result(),
        finalization_errors=(
            FinalizationError(
                stage="patch_export",
                code="PATCH_EXPORT_FAILED",
                message="export failed",
            ),
        ),
    )

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 1
    assert "answer\n" in invocation.stdout
    assert "PATCH_EXPORT_FAILED" in invocation.stderr


def test_cli_forwards_docker_workspace_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[BootstrapConfig] = []
    stub_cli_run(monkeypatch, successful_result(), configs)

    invocation = CliRunner().invoke(
        app,
        ["task", "--workspace", "docker", "--docker-image", "task-image"],
    )

    assert invocation.exit_code == 0
    assert len(configs) == 1
    assert configs[0].workspace_kind is cli_main.WorkspaceKind.DOCKER
    assert configs[0].docker_image == "task-image"


def test_cli_model_override_preserves_generation_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[BootstrapConfig] = []
    stub_cli_run(monkeypatch, successful_result(), configs)

    invocation = CliRunner().invoke(app, ["task", "--model", "replacement"])

    assert invocation.exit_code == 0
    assert configs[0].agent.model.model == "replacement"
    assert configs[0].agent.model.provider == cli_main.DEFAULT_AGENT.model.provider
    assert configs[0].agent.generation == cli_main.DEFAULT_AGENT.generation


def test_unknown_requests_are_not_rendered_as_zero_cost(monkeypatch):
    result = successful_result()
    result = result.model_copy(
        update={
            "usage": result.usage.model_copy(update={"unconfirmed_model_requests": 2})
        }
    )
    stub_cli_run(monkeypatch, result)
    invocation = CliRunner().invoke(app, ["task"])
    assert invocation.exit_code == 0
    assert "Cost (confirmed portion)" in invocation.stdout
    assert (
        "Unconfirmed model requests: 2; additional charges may apply"
        in invocation.stdout
    )
