import asyncio
import os
import signal
import sys
import traceback
from collections import Counter
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import typer
from pydantic import BaseModel, Field

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.bootstrap import execute_new_run
from miniagent.cli.main import parse_config
from miniagent.cli.settings import RunLocationSettings, Settings
from miniagent.cli.signals import run_with_signals
from miniagent.environments import WorkspaceKind
from miniagent.evaluation.swebench_data import SWEbenchTaskContext
from miniagent.runtime.history import iter_run_events
from miniagent.runtime.store_contracts import RunNotFoundError
from miniagent.storage import SQLiteRunStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
app = typer.Typer(pretty_exceptions_enable=False)


class EvalTask(BaseModel):
    instance_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    selection: Literal["development", "evaluation"]
    problem_statement: str = Field(min_length=1)
    image: str = Field(pattern=r"^docker\.io/.+@sha256:[0-9a-f]{64}$")
    platform: Literal["linux/amd64"]


class TaskManifest(BaseModel):
    tasks: list[EvalTask]


class TaskResult(BaseModel):
    instance_id: str
    run_id: UUID | None = None
    status: str = "error"
    passed: bool = False
    cost_usd: Decimal | None = None
    unconfirmed_model_requests: int = 0
    model_requests: int | None = None
    compaction_attempts: int | None = None
    compactions: int | None = None
    tool_executions: int | None = None
    steps: int | None = None
    trajectory: str | None = None
    log: str | None = None
    error: str | None = None


class BatchSummary(BaseModel):
    model: str
    total: int
    passed: int
    pass_rate: float
    known_cost_usd: Decimal
    unknown_cost_tasks: int
    model_requests: int
    compaction_attempts: int
    compactions: int
    tool_executions: int
    steps: int
    incomplete_statistics_tasks: int
    tasks: list[TaskResult]


def select_tasks(
    tasks: list[EvalTask], target: str | None, instance_ids: list[str]
) -> list[EvalTask]:
    if target is not None and target != "all":
        raise ValueError("The only positional selection is 'all'; use --instance-id")
    if target == "all":
        if instance_ids:
            raise ValueError("Cannot combine all with --instance-id")
        selected = [task for task in tasks if task.selection == "evaluation"]
    else:
        by_id = {task.instance_id: task for task in tasks}
        selected = []
        for instance_id in dict.fromkeys(instance_ids):
            if instance_id not in by_id:
                raise ValueError(f"Unknown instance ID: {instance_id}")
            task = by_id[instance_id]
            if task.selection != "evaluation":
                raise ValueError(f"Only evaluation tasks are allowed: {instance_id}")
            selected.append(task)
    if not selected:
        raise ValueError(
            "Select at least one evaluation task using --instance-id or all"
        )
    return selected


def create_output_directory(root: Path, now: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    name = now.strftime("%Y-%m-%d_%H-%M")
    suffix = 0
    while True:
        directory = root / (name if suffix == 0 else f"{name}_{suffix}")
        try:
            directory.mkdir()
            return directory
        except FileExistsError:
            suffix += 1


def build_summary(results: list[TaskResult], model: str) -> BatchSummary:
    passed = sum(result.passed for result in results)
    counters = (
        "model_requests",
        "compaction_attempts",
        "compactions",
        "tool_executions",
        "steps",
    )
    return BatchSummary(
        model=model,
        total=len(results),
        passed=passed,
        pass_rate=passed / len(results) if results else 0,
        known_cost_usd=sum(
            (result.cost_usd for result in results if result.cost_usd is not None),
            Decimal(0),
        ),
        unknown_cost_tasks=sum(
            result.cost_usd is None or result.unconfirmed_model_requests > 0
            for result in results
        ),
        incomplete_statistics_tasks=sum(
            any(getattr(result, key) is None for key in counters) for result in results
        ),
        tasks=results,
        **{
            key: sum(getattr(result, key) or 0 for result in results)
            for key in counters
        },
    )


async def collect_result(
    instance_id: str,
    run_id: UUID,
    database: Path,
    artifacts: FileArtifactStore,
    output: Path,
) -> TaskResult:
    store = SQLiteRunStore(database, read_only=True)
    record = await store.get(run_id)
    state = record.state
    counts: Counter[str] = Counter()
    async for event in iter_run_events(
        store, run_id, through_sequence=record.last_event_sequence
    ):
        counts[event.type.value] += 1
    evaluation = state.execution.evaluation.result
    result = TaskResult(
        instance_id=instance_id,
        run_id=run_id,
        status=state.lifecycle.status,
        passed=evaluation is not None and evaluation.passed,
        cost_usd=state.usage.cost_usd,
        unconfirmed_model_requests=state.usage.unconfirmed_model_requests
        + int(
            state.execution.active_operation is not None
            and state.execution.active_operation.kind in ("model", "summary")
        ),
        model_requests=counts["model_requested"] + counts["compaction_requested"],
        compaction_attempts=counts["compaction_requested"],
        compactions=counts["compaction_completed"],
        tool_executions=counts["tool_execution_start"],
        steps=counts["step_started"],
        error=state.lifecycle.error.message if state.lifecycle.error else None,
    )
    try:
        cursor = None
        trajectory = None
        while True:
            page = await artifacts.list_records_for_run(run_id, cursor=cursor)
            for artifact in page.records:
                if artifact.type == "run_trajectory":
                    trajectory = artifact.reference()
            cursor = page.next_cursor
            if cursor is None:
                break
        if trajectory is None:
            result.error = "; ".join(
                filter(None, [result.error, "Trajectory unavailable"])
            )
        else:
            filename = f"{instance_id}.trajectory.json"
            (output / filename).write_bytes(await artifacts.read(trajectory))
            result.trajectory = filename
    except Exception as exc:  # noqa: BLE001 -- preserve statistics when artifact collection fails.
        result.error = "; ".join(
            filter(None, [result.error, f"Trajectory copy failed: {exc}"])
        )
    return result


async def run_worker(task: EvalTask, model: str, output: Path) -> None:
    run_id = uuid4()
    result = TaskResult(instance_id=task.instance_id, run_id=run_id)
    try:
        config = replace(
            parse_config(
                task=(
                    "Reproduce the problem, implement the minimal fix, "
                    f"and run the relevant tests.\n\n{task.problem_statement}"
                ),
                model=model,
                workspace=WorkspaceKind.DOCKER,
                docker_image=task.image,
                swebench_task=SWEbenchTaskContext(instance_id=task.instance_id),
                trajectory=True,
            ),
            run_id=run_id,
        )
        application_result, _ = await run_with_signals(
            lambda cancellation, interruption: execute_new_run(
                config, cancellation_token=cancellation, interruption_token=interruption
            )
        )
        if application_result.finalization_errors:
            result.error = "; ".join(
                error.message for error in application_result.finalization_errors
            )
    except Exception as exc:  # noqa: BLE001 -- isolate task failure and publish its result.
        traceback.print_exc()
        result.error = f"{type(exc).__name__}: {exc}"
    try:
        database = RunLocationSettings().run_database_path
        if database.exists():
            collected = await collect_result(
                task.instance_id,
                run_id,
                database,
                FileArtifactStore(Settings().artifact_root),
                output,
            )
            collected.error = (
                "; ".join(filter(None, [result.error, collected.error])) or None
            )
            result = collected
    except RunNotFoundError:
        # Preparation can fail before the Run is committed.
        pass
    except Exception as exc:  # noqa: BLE001 -- retain task identity if evidence cannot be read.
        traceback.print_exc()
        result.error = "; ".join(
            filter(None, [result.error, f"Statistics failed: {exc}"])
        )
    path = output / f"{task.instance_id}.result.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(result.model_dump_json(indent=2) + "\n")
    temporary.replace(path)


async def run_jobs(
    commands: list[list[str]], output: Path, concurrency: int
) -> list[int]:
    if not 1 <= concurrency <= 3:
        raise ValueError("Concurrency must be between 1 and 3")
    semaphore = asyncio.Semaphore(concurrency)

    async def run(index: int, command: list[str]) -> int:
        async with semaphore:
            with (output / f"{index}.log").open("w") as log:
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=log,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    log.write(f"Worker launch failed: {exc}\n")
                    return 127
                try:
                    return await process.wait()
                except asyncio.CancelledError:
                    if process.returncode is None:
                        process.send_signal(signal.SIGINT)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=30)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                    raise

    loop = asyncio.get_running_loop()
    current = asyncio.current_task()
    assert current is not None
    loop.add_signal_handler(signal.SIGTERM, current.cancel)
    try:
        return list(
            await asyncio.gather(
                *(run(i, command) for i, command in enumerate(commands))
            )
        )
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def print_summary(summary: BatchSummary, output: Path) -> None:
    typer.echo(f"Pass rate: {summary.passed}/{summary.total} ({summary.pass_rate:.2%})")
    typer.echo(
        "Task\tPassed\tCost (USD)\tModel requests\tCompactions (attempts/success)\tTools\tSteps"
    )
    for result in summary.tasks:
        values = [
            result.instance_id,
            "yes" if result.passed else "no",
            f"{result.cost_usd} (partial; {result.unconfirmed_model_requests} unconfirmed requests)"
            if result.cost_usd is not None and result.unconfirmed_model_requests
            else result.cost_usd,
            result.model_requests,
            f"{result.compaction_attempts}/{result.compactions}"
            if result.compaction_attempts is not None
            else None,
            result.tool_executions,
            result.steps,
        ]
        typer.echo(
            "\t".join("unknown" if value is None else str(value) for value in values)
        )
        if result.error:
            typer.echo(f"  Error: {result.error}")
    typer.echo(
        f"Totals (available statistics): cost=${summary.known_cost_usd}; "
        f"model requests={summary.model_requests}; "
        f"compactions={summary.compaction_attempts}/{summary.compactions}; "
        f"tools={summary.tool_executions}; steps={summary.steps}"
    )
    if summary.unknown_cost_tasks or summary.incomplete_statistics_tasks:
        typer.echo(
            f"Unknown cost tasks: {summary.unknown_cost_tasks}; incomplete statistics tasks: {summary.incomplete_statistics_tasks}"
        )
    typer.echo(f"Results: {output}")


async def execute_batch(
    tasks: list[EvalTask], model: str, output: Path, concurrency: int
) -> BatchSummary:
    commands = [
        [
            sys.executable,
            "-m",
            "miniagent.cli.eval_batch",
            "--worker",
            "--instance-id",
            task.instance_id,
            "--model",
            model,
            "--output-directory",
            str(output),
        ]
        for task in tasks
    ]
    results = [
        TaskResult(instance_id=task.instance_id, status="not_started") for task in tasks
    ]
    (output / "selection.json").write_text(
        TaskManifest(tasks=tasks).model_dump_json(indent=2) + "\n"
    )
    try:
        codes = await run_jobs(commands, output, concurrency)
    finally:
        for index, task in enumerate(tasks):
            path = output / f"{task.instance_id}.result.json"
            if path.exists():
                results[index] = TaskResult.model_validate_json(path.read_text())
            elif (output / f"{index}.log").exists():
                results[index].status = "error"
                results[index].error = "Worker exited without a result; see task log"
            results[index].log = (
                f"{index}.log" if (output / f"{index}.log").exists() else None
            )
        summary = build_summary(results, model)
        (output / "summary.json").write_text(summary.model_dump_json(indent=2) + "\n")
        print_summary(summary, output)
    for result, code in zip(summary.tasks, codes, strict=True):
        if code:
            typer.echo(f"Worker exit code for {result.instance_id}: {code}", err=True)
    return summary


@app.command()
def main(
    target: Annotated[
        str | None, typer.Argument(help="Use all to execute every evaluation task.")
    ] = None,
    instance_id: Annotated[
        list[str] | None,
        typer.Option(help="Evaluation instance ID; repeat for multiple tasks."),
    ] = None,
    model: Annotated[
        str,
        typer.Option(envvar="MINIAGENT_EVAL_MODEL", help="Model used by miniagent."),
    ] = DEFAULT_AGENT.model.model,
    concurrency: Annotated[
        int, typer.Option(min=1, max=3, help="Maximum concurrent tasks.")
    ] = 3,
    worker: Annotated[bool, typer.Option(hidden=True)] = False,
    output_directory: Annotated[Path | None, typer.Option(hidden=True)] = None,
) -> None:
    """Run SWE-bench evaluation tasks and collect trajectories and statistics."""
    os.chdir(PROJECT_ROOT)
    try:
        manifest = TaskManifest.model_validate_json(
            (PROJECT_ROOT / "docs/swebench-task-base.json").read_text()
        )
        tasks = select_tasks(manifest.tasks, target, instance_id or [])
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    if worker:
        if len(tasks) != 1 or output_directory is None:
            raise typer.BadParameter("Worker requires one task and an output directory")
        asyncio.run(run_worker(tasks[0], model, output_directory))
        return
    output = create_output_directory(PROJECT_ROOT / "eval", datetime.now().astimezone())
    typer.echo(f"Selected {len(tasks)} tasks; concurrency={concurrency}; model={model}")
    typer.echo(f"Results: {output}")
    try:
        summary = asyncio.run(execute_batch(tasks, model, output, concurrency))
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None
    except asyncio.CancelledError:
        raise typer.Exit(code=143) from None
    if any(result.error for result in summary.tasks) or summary.passed != summary.total:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
