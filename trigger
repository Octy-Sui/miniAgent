#!/usr/bin/env -S uv run python

import os
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, Field

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
def main(
    instance_id: Annotated[
        str, typer.Option(help="SWE-bench development instance ID.")
    ],
    model: Annotated[
        str,
        typer.Option(help="Model used by miniagent.", envvar="MINIAGENT_TRIGGER_MODEL"),
    ] = "deepseek-v4-pro",
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
        os.execvp(
            "miniagent",
            [
                "miniagent",
                (
                    "Reproduce the problem, implement the minimal fix, "
                    f"and run the relevant tests.\n\n{task.problem_statement}"
                ),
                "--workspace",
                "docker",
                "--docker-image",
                task.image,
                "--model",
                model,
            ],
        )
    except OSError as exc:
        typer.echo(f"Cannot launch miniagent: {exc}", err=True)
        raise typer.Exit(code=1) from exc


if __name__ == "__main__":
    app()
