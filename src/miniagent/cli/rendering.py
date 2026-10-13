import typer

from miniagent.artifacts import ArtifactRef
from miniagent.cli.application import ApplicationResult
from miniagent.runtime import RunResult, RunStatus


def print_patch_artifact(patch_artifact: ArtifactRef | None) -> None:
    if patch_artifact is not None:
        typer.echo(f"Patch: {patch_artifact.uri}")


def print_run_result(
    result: RunResult,
    *,
    failure_exit_code: int,
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
        typer.echo(result.error.message[:4096], err=True)
    print_patch_artifact(patch_artifact)
    raise typer.Exit(code=failure_exit_code)


def print_application_result(
    result: ApplicationResult,
    *,
    failure_exit_code: int,
) -> None:
    run_succeeded = result.run_result.status is RunStatus.SUCCEEDED
    if not run_succeeded:
        _print_finalization_errors(result)
    print_run_result(
        result.run_result,
        failure_exit_code=failure_exit_code,
        patch_artifact=next(
            (ref for ref in result.artifacts if ref.type == "workspace_patch"), None
        ),
    )
    if run_succeeded and result.finalization_errors:
        _print_finalization_errors(result)
        raise typer.Exit(code=failure_exit_code)


def _print_finalization_errors(result: ApplicationResult) -> None:
    for error in result.finalization_errors:
        typer.echo(
            f"Finalization failed ({error.stage}, {error.code}): {error.message}"[
                :4096
            ],
            err=True,
        )
