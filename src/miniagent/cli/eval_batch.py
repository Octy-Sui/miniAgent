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
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

import typer
import yaml
from pydantic import BaseModel, Field

from miniagent.agents.default_agent import DEFAULT_MODEL
from miniagent.artifacts import FileArtifactStore
from miniagent.cli.bootstrap import execute_new_run
from miniagent.cli.eval_metrics import (
    CallStatistics,
    MetricsCollector,
    ModelStatistics,
    ToolStatistics,
    summarize_models,
    summarize_tools,
)
from miniagent.cli.logging import logging_session
from miniagent.cli.main import parse_config
from miniagent.cli.prompts import PromptConfig, PromptConfigError, load_prompts
from miniagent.cli.settings import RunLocationSettings, Settings
from miniagent.cli.signals import run_with_signals
from miniagent.diagnostics import log_event, operation
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
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model_requests: int | None = None
    compaction_attempts: int | None = None
    compactions: int | None = None
    tool_executions: int | None = None
    steps: int | None = None
    trajectory: str | None = None
    log: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    model_metrics: ModelStatistics | None = None
    tool_metrics: ToolStatistics | None = None
    run_duration_ms: float | None = None
    task_duration_ms: float | None = None


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
    model_metrics: ModelStatistics
    tool_metrics: ToolStatistics
    metrics_unavailable_tasks: int
    wall_duration_ms: float | None = None


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
        model_metrics=summarize_models(
            [
                call
                for result in results
                if result.model_metrics is not None
                for call in result.model_metrics.calls
            ]
        ),
        tool_metrics=summarize_tools(
            [
                call
                for result in results
                if result.tool_metrics is not None
                for call in result.tool_metrics.calls
            ],
            dict(
                sum(
                    (
                        Counter(result.tool_metrics.not_executed)
                        for result in results
                        if result.tool_metrics is not None
                    ),
                    Counter(),
                )
            ),
        ),
        metrics_unavailable_tasks=sum(
            result.model_metrics is None or result.tool_metrics is None
            for result in results
        ),
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
    evaluation = state.execution.evaluation.result
    result = TaskResult(
        run_duration_ms=(
            (
                (state.lifecycle.finished_at or state.lifecycle.updated_at)
                - state.lifecycle.started_at
            ).total_seconds()
            * 1000
            if state.lifecycle.started_at is not None
            else None
        ),
        instance_id=instance_id,
        run_id=run_id,
        status=state.lifecycle.status,
        passed=evaluation is not None and evaluation.passed,
        cost_usd=state.usage.cost_usd,
        input_tokens=state.usage.input_tokens,
        cached_input_tokens=state.usage.cached_input_tokens,
        output_tokens=state.usage.output_tokens,
        unconfirmed_model_requests=state.usage.unconfirmed_model_requests
        + int(
            state.execution.active_operation is not None
            and state.execution.active_operation.kind in ("model", "summary")
        ),
        error=state.lifecycle.error.message if state.lifecycle.error else None,
    )
    try:
        counts: Counter[str] = Counter()
        metrics = MetricsCollector()
        async for event in iter_run_events(
            store, run_id, through_sequence=record.last_event_sequence
        ):
            counts[event.type.value] += 1
            metrics.observe(event)
        result.model_metrics = metrics.models()
        result.tool_metrics = metrics.tools()
        result.model_requests = (
            counts["model_requested"] + counts["compaction_requested"]
        )
        result.compaction_attempts = counts["compaction_requested"]
        result.compactions = counts["compaction_completed"]
        result.tool_executions = counts["tool_execution_start"]
        result.steps = counts["step_started"]
    except Exception as exc:  # noqa: BLE001 -- Optional projections cannot replace the stored Run result.
        result.model_metrics = result.tool_metrics = None
        result.warnings.append(f"Statistics unavailable: {type(exc).__name__}: {exc}")
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
            result.warnings.append("Trajectory unavailable")
        else:
            filename = f"{instance_id}.trajectory.json"
            (output / filename).write_bytes(await artifacts.read(trajectory))
            result.trajectory = filename
    except Exception as exc:  # noqa: BLE001 -- preserve statistics when artifact collection fails.
        result.warnings.append(f"Trajectory copy failed: {exc}")
    return result


async def run_worker(
    task: EvalTask, model: str, output: Path, prompts: PromptConfig | None = None
) -> None:
    with logging_session():
        started = perf_counter()
        run_id = uuid4()
        result = TaskResult(instance_id=task.instance_id, run_id=run_id)
        try:
            config = replace(
                parse_config(
                    task=task.problem_statement,
                    model=model,
                    workspace=WorkspaceKind.DOCKER,
                    docker_image=task.image,
                    swebench_task=SWEbenchTaskContext(instance_id=task.instance_id),
                    trajectory=True,
                    prompts=prompts,
                ),
                run_id=run_id,
            )
            application_result, _ = await run_with_signals(
                lambda cancellation, interruption: execute_new_run(
                    config,
                    cancellation_token=cancellation,
                    interruption_token=interruption,
                )
            )
            if application_result.finalization_errors:
                result.warnings.extend(
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
                collected.warnings.extend(result.warnings)
                result = collected
        except RunNotFoundError:
            # Preparation can fail before the Run is committed.
            pass
        except Exception as exc:  # noqa: BLE001 -- retain task identity if evidence cannot be read.
            traceback.print_exc()
            result.error = "; ".join(
                filter(None, [result.error, f"Statistics failed: {exc}"])
            )
        result.task_duration_ms = (perf_counter() - started) * 1000
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
    running = 0
    completed = 0
    log_event("batch.started", total=len(commands), output_directory=output)

    async def run(index: int, command: list[str]) -> int:
        nonlocal running, completed
        log_event("batch.worker_queued", worker_index=index, total=len(commands))
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
                    completed += 1
                    log_event(
                        "batch.worker_failed",
                        worker_index=index,
                        exit_code=127,
                        completed=completed,
                        total=len(commands),
                        running=running,
                    )
                    return 127
                running += 1
                log_event(
                    "batch.worker_started",
                    worker_index=index,
                    running=running,
                    total=len(commands),
                    log_path=output / f"{index}.log",
                )
                try:
                    async with operation(
                        "batch_worker", lifecycle=False, worker_index=index
                    ):
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
                finally:
                    running -= 1
                    completed += 1
                    log_event(
                        "batch.worker_completed",
                        worker_index=index,
                        running=running,
                        completed=completed,
                        total=len(commands),
                        exit_code=process.returncode,
                    )

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


def _print_call_statistics(label: str, stats: CallStatistics) -> None:
    rate = f"{stats.success_rate:.2%}" if stats.success_rate is not None else "unknown"
    typer.echo(
        f"  {label}: success={stats.succeeded}/{stats.started} ({rate}); failed={stats.failed}; interrupted={stats.interrupted}; unknown={stats.unknown}"
    )
    for name, durations in (
        ("RT/execution duration ms", stats.duration_ms),
        ("Server duration ms", stats.server_duration_ms),
        ("Event interval ms (approximate)", stats.event_elapsed_ms),
    ):
        if durations.samples:
            typer.echo(
                f"    {name}: n={durations.samples}; total={durations.total:.2f}; mean={durations.mean:.2f}; p50={durations.p50:.2f}; p95={durations.p95:.2f}; max={durations.maximum:.2f}"
            )
    if not stats.duration_ms.samples:
        typer.echo("    RT/execution duration ms: unknown (n=0)")


def _print_metrics(
    models: ModelStatistics | None, tools: ToolStatistics | None
) -> None:
    if models is not None:
        _print_call_statistics("Model requests", models.overall)
        if models.business.started:
            _print_call_statistics("Business requests", models.business)
        if models.compaction.started:
            _print_call_statistics("Compaction requests", models.compaction)
    if tools is not None:
        _print_call_statistics("Tool executions", tools.overall)
        for name, stats in tools.by_tool.items():
            _print_call_statistics(f"Tool {name}", stats)
        if tools.not_executed:
            typer.echo(f"  Tools not executed (by error/status): {tools.not_executed}")


def print_summary(summary: BatchSummary, output: Path) -> None:
    typer.echo(f"Pass rate: {summary.passed}/{summary.total} ({summary.pass_rate:.2%})")
    typer.echo(
        "Task\tPassed\tCost (USD)\tModel requests\tCompactions (attempts/success)\tTools\tSteps\tInput tokens (incl. cache)\tCache tokens\tOutput tokens"
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
            result.input_tokens,
            result.cached_input_tokens,
            result.output_tokens,
        ]
        typer.echo(
            "\t".join("unknown" if value is None else str(value) for value in values)
        )
        if result.unconfirmed_model_requests:
            typer.echo(
                f"  Token usage is partial: {result.unconfirmed_model_requests} requests with unconfirmed usage"
            )
        if result.error:
            typer.echo(f"  Error: {result.error}")
        for warning in result.warnings:
            typer.echo(f"  Warning: {warning}")
        if result.task_duration_ms is not None:
            typer.echo(
                f"  Task total ms (including preparation/cleanup): {result.task_duration_ms:.2f}"
            )
        if result.run_duration_ms is not None:
            typer.echo(f"  Run elapsed ms: {result.run_duration_ms:.2f}")
        _print_metrics(result.model_metrics, result.tool_metrics)
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
    typer.echo("Batch request/tool statistics:")
    _print_metrics(summary.model_metrics, summary.tool_metrics)
    if summary.metrics_unavailable_tasks:
        typer.echo(
            f"Tasks with unavailable request/tool metrics: {summary.metrics_unavailable_tasks}"
        )
    if summary.wall_duration_ms is not None:
        typer.echo(f"Batch wall duration ms: {summary.wall_duration_ms:.2f}")
    typer.echo(f"Results: {output}")


async def execute_batch(
    tasks: list[EvalTask], model: str, output: Path, concurrency: int
) -> BatchSummary:
    with logging_session():
        started = perf_counter()
        prompts = load_prompts()
        prompt_snapshot = (output / "prompts.yaml").resolve()
        prompt_snapshot.write_text(
            yaml.safe_dump(prompts.model_dump(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
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
                "--prompt-file",
                str(prompt_snapshot),
            ]
            for task in tasks
        ]
        results = [
            TaskResult(instance_id=task.instance_id, status="not_started")
            for task in tasks
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
                    results[
                        index
                    ].error = "Worker exited without a result; see task log"
                results[index].log = (
                    f"{index}.log" if (output / f"{index}.log").exists() else None
                )
            summary = build_summary(results, model)
            summary.wall_duration_ms = (perf_counter() - started) * 1000
            (output / "summary.json").write_text(
                summary.model_dump_json(indent=2) + "\n"
            )
            print_summary(summary, output)
        for result, code in zip(summary.tasks, codes, strict=True):
            if code:
                typer.echo(
                    f"Worker exit code for {result.instance_id}: {code}", err=True
                )
        return summary


@app.command()
@logging_session()
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
    ] = DEFAULT_MODEL.model,
    concurrency: Annotated[
        int, typer.Option(min=1, max=3, help="Maximum concurrent tasks.")
    ] = 3,
    worker: Annotated[bool, typer.Option(hidden=True)] = False,
    output_directory: Annotated[Path | None, typer.Option(hidden=True)] = None,
    prompt_file: Annotated[Path | None, typer.Option(hidden=True)] = None,
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
        try:
            prompts = load_prompts(prompt_file)
        except PromptConfigError as exc:
            raise typer.BadParameter(str(exc)) from exc
        asyncio.run(run_worker(tasks[0], model, output_directory, prompts))
        return
    output = create_output_directory(PROJECT_ROOT / "eval", datetime.now().astimezone())
    typer.echo(f"Selected {len(tasks)} tasks; concurrency={concurrency}; model={model}")
    typer.echo(f"Results: {output}")
    try:
        summary = asyncio.run(execute_batch(tasks, model, output, concurrency))
    except PromptConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None
    except asyncio.CancelledError:
        raise typer.Exit(code=143) from None
    if any(result.error for result in summary.tasks) or summary.passed != summary.total:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
