# File   : main.py
# Created: 2026-09-10 18:46:44
# Author : octy Sui

import asyncio
import signal
from pathlib import Path
from typing import Annotated

import typer

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.agents.default import DEFAULT_AGENT
from miniagent.models import Settings, load_price_config
from miniagent.providers import ModelProvider
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import (
    AgentRuntime,
    CancellationToken,
    RunResult,
    RunStatus,
)
from miniagent.tools import Tool
from miniagent.tools.read_file import ReadFileTool
from miniagent.tools.shell import ShellTool

app = typer.Typer()


def build_provider(agent: AgentSpec, settings: Settings) -> ModelProvider:
    if agent.model.provider == "qwen":
        return QwenProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
    raise ValueError(f"Unknown provider: {agent.model.provider}")


def build_tools(workspace: Path) -> dict[str, Tool]:
    return {
        "read_file": ReadFileTool(workspace),
        "shell": ShellTool(workspace),
    }


class _ForcedSignalCancellation(Exception):
    def __init__(self, received_signal: signal.Signals) -> None:
        self.received_signal = received_signal


async def run_agent(
    agent: AgentSpec,
    task: str,
    workspace: Path,
    cancellation_token: CancellationToken | None = None,
) -> RunResult:
    settings = Settings()
    runtime = AgentRuntime(
        provider=build_provider(agent, settings),
        tools=build_tools(workspace),
        cancellation_token=cancellation_token,
        pricing=load_price_config(settings.model_pricing_file),
    )
    return await runtime.run(agent, task)


async def _run_agent_with_signals(
    agent: AgentSpec,
    task: str,
    workspace: Path,
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
        result = await run_agent(
            agent,
            task,
            workspace,
            cancellation_token,
        )
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


@app.command()
def main(
    task: Annotated[str, typer.Argument(help="Task for the agent to complete.")],
    model: Annotated[
        str | None,
        typer.Option(help="Override the default agent model."),
    ] = None,
) -> None:
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

    try:
        result, received_signal = asyncio.run(
            _run_agent_with_signals(agent, task, Path.cwd())
        )
    except _ForcedSignalCancellation as exc:
        raise typer.Exit(code=_signal_exit_code(exc.received_signal)) from None
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise typer.Exit(code=130) from None

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
        return

    typer.echo(f"Agent stopped: {result.reason}", err=True)
    if result.error is not None:
        typer.echo(result.error.message, err=True)
    exit_code = _signal_exit_code(received_signal) if received_signal is not None else 1
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
