# File   : main.py
# Created: 2026-09-10 18:46:44
# Author : octy Sui

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from miniagent.agents import AgentSpec, ModelTarget
from miniagent.agents.default import DEFAULT_AGENT
from miniagent.config import Settings
from miniagent.providers import ModelProvider
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import AgentRuntime, RunResult, RunStatus
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


async def run_agent(agent: AgentSpec, task: str, workspace: Path) -> RunResult:
    runtime = AgentRuntime(
        provider=build_provider(agent, Settings()),
        tools=build_tools(workspace),
    )
    return await runtime.run(agent, task)


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

    result = asyncio.run(run_agent(agent, task, Path.cwd()))
    if result.status is RunStatus.COMPLETED:
        typer.echo(result.output)
        return

    typer.echo(f"Agent stopped: {result.reason}", err=True)
    if result.error is not None:
        typer.echo(result.error.message, err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
