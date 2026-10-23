# File   : main.py
# Created: 2026-09-10 18:46:44
# Author : octy Sui
import asyncio
import signal
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.bootstrap import BootstrapConfig, execute_new_run
from miniagent.cli.console_approval import ConsoleApprovalHandler
from miniagent.cli.logging import logging_session
from miniagent.cli.model_limits import ModelLimitsError
from miniagent.cli.rendering import (
    print_application_result,
    print_resume_plan,
    print_resume_rejection,
    print_trajectory_exception,
)
from miniagent.cli.resume import ResumeConfig, resume_application
from miniagent.cli.settings import RunLocationSettings
from miniagent.cli.signals import _ForcedSignalCancellation, run_with_signals
from miniagent.environments import WorkspaceKind
from miniagent.environments.managed_execution import ManagedExecutionError
from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
)
from miniagent.models.config import ModelTarget
from miniagent.runtime.recording import RunPersistenceError
from miniagent.runtime.resume import ResumePlan, ResumeRejected, ResumeRejection
from miniagent.storage.run_lock import RunInUseError

app = typer.Typer()
run_app = typer.Typer()
resume_app = typer.Typer(add_completion=False)


def _signal_exit_code(received_signal: signal.Signals) -> int:
    return 128 + received_signal.value


def parse_config(
    task: str,
    model: str | None,
    workspace: WorkspaceKind | None,
    docker_image: str | None,
    *,
    swebench_task: SWEbenchTaskContext | None = None,
    trajectory: bool | None = None,
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
        trajectory=trajectory,
    )


async def main(
    task: str,
    model: str | None = None,
    workspace: WorkspaceKind | None = None,
    docker_image: str | None = None,
    *,
    swebench_task: SWEbenchTaskContext | None = None,
    trajectory: bool | None = None,
) -> None:
    with logging_session():
        config = parse_config(
            task,
            model,
            workspace,
            docker_image,
            swebench_task=swebench_task,
            trajectory=trajectory,
        )

        try:
            result, received_signal = await run_with_signals(
                lambda cancellation, interruption: execute_new_run(
                    config,
                    cancellation_token=cancellation,
                    interruption_token=interruption,
                )
            )
        except EvaluationPreparationError as exc:
            typer.echo(str(exc)[:4096], err=True)
            raise typer.Exit(code=1) from None
        except RunPersistenceError as exc:
            print_trajectory_exception(exc)
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        except ManagedExecutionError as exc:
            print_trajectory_exception(exc)
            typer.echo(
                "WORKSPACE_BUSY: execution end is unconfirmed; original workspace retained",
                err=True,
            )
            raise typer.Exit(code=1) from None
        except RunInUseError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        except ModelLimitsError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        except ValueError:
            typer.echo(
                "Configuration or dependency is unavailable or invalid", err=True
            )
            raise typer.Exit(code=1) from None
        except _ForcedSignalCancellation as exc:
            if isinstance(exc.__cause__, RunPersistenceError):
                typer.echo(str(exc.__cause__), err=True)
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
@run_app.command()
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
    trajectory: Annotated[
        bool | None,
        typer.Option(
            "--trajectory/--no-trajectory", help="Export committed run trajectory."
        ),
    ] = None,
) -> None:
    try:
        asyncio.run(main(task, model, workspace, docker_image, trajectory=trajectory))
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


def parse_run_id(value: str) -> UUID:
    return UUID(value.removeprefix("run_"))


async def _resume_main(config: ResumeConfig) -> None:
    with logging_session():
        try:
            result, received_signal = await run_with_signals(
                lambda cancellation, interruption: resume_application(
                    config,
                    cancellation_token=cancellation,
                    interruption_token=interruption,
                )
            )
        except ResumeRejected as exc:
            print_trajectory_exception(exc)
            print_resume_rejection(exc.rejection)
            raise typer.Exit(code=1) from None
        except RunInUseError:
            print_resume_rejection(
                ResumeRejection(
                    code="RUN_IN_USE",
                    run_id=config.run_id,
                    message="Run is in use by another process",
                )
            )
            raise typer.Exit(code=1) from None
        except RunPersistenceError as exc:
            print_trajectory_exception(exc)
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        except _ForcedSignalCancellation as exc:
            if isinstance(exc.__cause__, RunPersistenceError):
                typer.echo(str(exc.__cause__), err=True)
            raise typer.Exit(code=_signal_exit_code(exc.received_signal)) from None
        except asyncio.CancelledError:
            raise typer.Exit(code=130) from None
        except (OSError, ValueError, ManagedExecutionError) as exc:
            print_trajectory_exception(exc)
            typer.echo(
                "WORKSPACE_UNAVAILABLE: recovery could not continue; original workspace retained",
                err=True,
            )
            raise typer.Exit(code=1) from None
        if isinstance(result, ResumePlan):
            print_resume_plan(result)
            if received_signal is not None:
                raise typer.Exit(code=_signal_exit_code(received_signal))
        else:
            print_application_result(
                result,
                failure_exit_code=_signal_exit_code(received_signal)
                if received_signal is not None
                else 1,
            )


@resume_app.command()
def resume_cli(
    run_id: Annotated[
        str, typer.Option("--run-id", help="Complete UUID of the original Run.")
    ],
    database: Annotated[
        Path | None, typer.Option("--database", help="Original Run database.")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Inspect readiness without changing the Run."),
    ] = False,
) -> None:
    try:
        identity = parse_run_id(run_id)
    except ValueError:
        raise typer.BadParameter(
            "Expected a complete UUID", param_hint="--run-id"
        ) from None
    location = (
        database.expanduser().resolve()
        if database is not None
        else RunLocationSettings().run_database_path
    )
    try:
        asyncio.run(
            _resume_main(
                ResumeConfig(run_id=identity, database=location, dry_run=dry_run)
            )
        )
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


def entrypoint(argv: Sequence[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "run":
        run_app(args=arguments[1:], prog_name="miniagent run")
    elif arguments and arguments[0] == "resume":
        resume_app(args=arguments[1:], prog_name="miniagent resume")
    else:
        app(args=arguments, prog_name="miniagent")


if __name__ == "__main__":
    entrypoint()
