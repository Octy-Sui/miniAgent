# File   : main.py
# Created: 2026-09-10 18:46:44
# Author : octy Sui

import asyncio
import getpass
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import orjson
import typer

from miniagent.agents import ModelTarget
from miniagent.agents.default import DEFAULT_AGENT
from miniagent.artifacts import ArtifactRef
from miniagent.cli.bootstrap import (
    BootstrapConfig,
    BootstrappedAgent,
    bootstrap,
)
from miniagent.environments import WorkspaceKind
from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)
from miniagent.runtime import (
    CancellationToken,
    RunResult,
    RunStatus,
)

app = typer.Typer()


class ConsoleApprovalHandler:
    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution:
        typer.echo(
            f"Approval required ({decision.risk}): {decision.input_summary}",
            err=True,
        )
        typer.echo(
            orjson.dumps(
                decision.request.arguments,
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            ).decode(),
            err=True,
        )
        approved = await asyncio.to_thread(
            typer.confirm,
            "Allow this tool call?",
            default=False,
        )
        return ApprovalResolution(
            approval_id=request.approval_id,
            outcome=(
                ApprovalOutcome.APPROVED if approved else ApprovalOutcome.REJECTED
            ),
            actor_kind="human",
            actor_id=getpass.getuser(),
            reason="Approved in the CLI" if approved else "Rejected in the CLI",
            resolved_at=datetime.now(UTC),
        )


class _ForcedSignalCancellation(Exception):
    def __init__(self, received_signal: signal.Signals) -> None:
        self.received_signal = received_signal


async def run_with_signals(
    agent: BootstrappedAgent,
) -> tuple[RunResult, signal.Signals | None]:
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    cancellation_token = CancellationToken()
    received_signal: asyncio.Future[signal.Signals] = loop.create_future()

    def handle_signal(current_signal: signal.Signals) -> None:
        if received_signal.done():
            if main_task is not None:
                main_task.cancel()
            return

        received_signal.set_result(current_signal)
        cancellation_token.cancel()

    registered_signals: list[signal.Signals] = []
    for handled_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                handled_signal,
                handle_signal,
                handled_signal,
            )
        except NotImplementedError:
            continue
        registered_signals.append(handled_signal)

    try:
        result = await agent.run(cancellation_token)
        return (
            result,
            received_signal.result() if received_signal.done() else None,
        )
    except asyncio.CancelledError as exc:
        if received_signal.done():
            raise _ForcedSignalCancellation(received_signal.result()) from exc
        raise
    finally:
        for registered_signal in registered_signals:
            loop.remove_signal_handler(registered_signal)


def _signal_exit_code(received_signal: signal.Signals) -> int:
    return 128 + received_signal.value


def print_patch_artifact(patch_artifact: ArtifactRef | None) -> None:
    if patch_artifact is not None:
        typer.echo(f"Patch: {patch_artifact.uri}")


def print_run_result(
    result: RunResult,
    received_signal: signal.Signals | None,
    patch_artifact: ArtifactRef | None = None,
) -> None:
    if result.status is RunStatus.SUCCEEDED:
        typer.echo(result.output)
        typer.echo(
            "Token usage: "
            f"total={result.usage.total_tokens} "
            f"input={result.usage.input_tokens} "
            f"(+{result.usage.cached_input_tokens} cached) "
            f"output={result.usage.output_tokens} "
            f"({result.usage.reasoning_output_tokens} reasoning)"
        )
        if result.usage.cached_input_priced_separately:
            typer.echo(
                "Cost: "
                f"total=${result.usage.cost_usd:.8f} "
                f"uncached_input=${result.usage.input_cost_usd:.8f} "
                f"cached=${result.usage.cached_input_cost_usd:.8f} "
                f"output=${result.usage.output_cost_usd:.8f} USD"
            )
        else:
            typer.echo(
                "Cost: "
                f"total=${result.usage.cost_usd:.8f} "
                f"input=${result.usage.input_cost_usd:.8f} "
                f"output=${result.usage.output_cost_usd:.8f} USD"
            )
        print_patch_artifact(patch_artifact)
        return

    typer.echo(f"Agent stopped: {result.reason}", err=True)
    if result.error is not None:
        typer.echo(result.error.message, err=True)
    print_patch_artifact(patch_artifact)
    exit_code = _signal_exit_code(received_signal) if received_signal is not None else 1
    raise typer.Exit(code=exit_code)


def parse_config(
    task: str,
    model: str | None,
    workspace: WorkspaceKind | None,
    docker_image: str | None,
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
    )


async def main(
    task: str,
    model: str | None = None,
    workspace: WorkspaceKind | None = None,
    docker_image: str | None = None,
) -> None:
    config = parse_config(task, model, workspace, docker_image)

    try:
        async with bootstrap(config) as agent:
            result, received_signal = await run_with_signals(agent)
    except _ForcedSignalCancellation as exc:
        raise typer.Exit(code=_signal_exit_code(exc.received_signal)) from None
    except asyncio.CancelledError:
        raise typer.Exit(code=130) from None

    print_run_result(result, received_signal, agent.patch_artifact)


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
