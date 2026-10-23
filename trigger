#!/usr/bin/env -S uv run python

import asyncio
import os
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, Field

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.logging import logging_session
from miniagent.cli.main import main as run_agent
from miniagent.environments import WorkspaceKind
from miniagent.evaluation.swebench_data import SWEbenchTaskContext

PROJECT_ROOT = Path(__file__).resolve().parent
app = typer.Typer()


class Task(BaseModel):
    instance_id: str
    selection: Literal["development", "evaluation"]
    problem_statement: str = Field(min_length=1)
    image: str = Field(pattern=r"^docker\.io/.+@sha256:[0-9a-f]{64}$")


class TaskManifest(BaseModel):
    tasks: list[Task]


@app.command()
@logging_session()
def main(
    instance_id: Annotated[
        str, typer.Option(help="SWE-bench development instance ID.")
    ],
    model: Annotated[
        str,
        typer.Option(help="Model used by miniagent.", envvar="MINIAGENT_TRIGGER_MODEL"),
    ] = DEFAULT_AGENT.model.model,
    trajectory: Annotated[
        bool | None,
        typer.Option(
            "--trajectory/--no-trajectory", help="Export committed run trajectory."
        ),
    ] = None,
) -> None:
    """Run a selected development task using its pinned Docker image."""
    try:
        manifest = TaskManifest.model_validate_json(
            (PROJECT_ROOT / "docs/swebench-task-base.json").read_text()
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Cannot load task manifest: {exc}") from exc

    task = next(
        (task for task in manifest.tasks if task.instance_id == instance_id), None
    )
    if task is None:
        raise typer.BadParameter(
            f"Unknown instance ID: {instance_id}", param_hint="--instance-id"
        )
    if task.selection != "development":
        raise typer.BadParameter(
            "This script runs development tasks only.", param_hint="--instance-id"
        )

    os.chdir(PROJECT_ROOT)
    try:
        asyncio.run(
            run_agent(
                task=(
                    "Reproduce the problem, implement the minimal fix, "
                    f"and run the relevant tests.\n\n{task.problem_statement}"
                ),
                workspace=WorkspaceKind.DOCKER,
                docker_image=task.image,
                model=model,
                swebench_task=SWEbenchTaskContext(instance_id=task.instance_id),
                trajectory=trajectory,
            )
        )
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


if __name__ == "__main__":
    app()
