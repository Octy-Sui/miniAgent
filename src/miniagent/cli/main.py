# File   : main.py
# Created: 2026-09-10 18:46:44
# Author : octy Sui

import asyncio
import signal
from pathlib import Path
from typing import Annotated

import typer

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.bootstrap import BootstrapConfig, execute_new_run
from miniagent.cli.console_approval import ConsoleApprovalHandler
from miniagent.cli.rendering import print_application_result
from miniagent.cli.signals import _ForcedSignalCancellation, run_with_signals
from miniagent.environments import WorkspaceKind
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
)
from miniagent.models.config import ModelTarget

app = typer.Typer()


def _signal_exit_code(received_signal: signal.Signals) -> int:
    return 128 + received_signal.value


def parse_config(
    task: str,
    model: str | None,
    workspace: WorkspaceKind | None,
    docker_image: str | None,
    *,
    swebench_task: SWEbenchTaskContext | None = None,
) -> BootstrapConfig:
    agent = DEFAULT_AGENT
    if model is not None:
        agent = agent.model_copy(
            update={
                "model": ModelTarget(
                    provider=agent.model.provider,
                    model=model,
                )
            }
        )
    return BootstrapConfig(
        agent=agent,
        task=task,
        source_dir=Path.cwd(),
        workspace_kind=workspace,
        docker_image=docker_image,
        approval_handler=ConsoleApprovalHandler(),
        swebench_task=swebench_task,
    )


async def main(
    task: str,
    model: str | None = None,
    workspace: WorkspaceKind | None = None,
    docker_image: str | None = None,
    *,
    swebench_task: SWEbenchTaskContext | None = None,
) -> None:
    config = parse_config(
        task, model, workspace, docker_image, swebench_task=swebench_task
    )

    try:
        result, received_signal = await run_with_signals(
            lambda cancellation, interruption: execute_new_run(
                config, cancellation_token=cancellation, interruption_token=interruption
            )
        )
    except EvaluationPreparationError as exc:
        typer.echo(str(exc)[:4096], err=True)
        raise typer.Exit(code=1) from None
    except _ForcedSignalCancellation as exc:
        raise typer.Exit(code=_signal_exit_code(exc.received_signal)) from None
    except asyncio.CancelledError:
        raise typer.Exit(code=130) from None

    print_application_result(
        result,
        failure_exit_code=(
            _signal_exit_code(received_signal) if received_signal is not None else 1
        ),
    )


@app.command()
def cli(
    task: Annotated[str, typer.Argument(help="Task for the agent to complete.")],
    model: Annotated[
        str | None,
        typer.Option(help="Override the default agent model."),
    ] = None,
    workspace: Annotated[
        WorkspaceKind | None,
        typer.Option("--workspace", help="Workspace backend: local or docker."),
    ] = None,
    docker_image: Annotated[
        str | None,
        typer.Option(help="Docker image used by the docker workspace backend."),
    ] = None,
) -> None:
    try:
        asyncio.run(main(task, model, workspace, docker_image))
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


if __name__ == "__main__":
    app()
