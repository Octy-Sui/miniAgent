import typer

from miniagent.artifacts import ArtifactRef
from miniagent.cli.application import ApplicationResult
from miniagent.runtime import RunResult, RunStatus, RunSuspension
from miniagent.runtime.resume import ResumePlan, ResumeRejection


def print_patch_artifact(patch_artifact: ArtifactRef | None) -> None:
    if patch_artifact is not None:
        typer.echo(f"Patch: {patch_artifact.uri}")


def print_run_result(
    result: RunResult | RunSuspension,
    *,
    failure_exit_code: int,
    patch_artifact: ArtifactRef | None = None,
) -> None:
    if isinstance(result, RunSuspension):
        typer.echo(f"Run suspended: {result.run_id}")
        if result.workspace_id is not None:
            typer.echo(f"Workspace retained: {result.workspace_id}")
        typer.echo(f"Confirmed cost: ${result.usage.cost_usd:.8f} USD")
        typer.echo(
            "Unconfirmed model requests may be billed separately; confirmed costs exclude them."
        )
        print_patch_artifact(patch_artifact)
        raise typer.Exit(code=128 + result.interruption.signal_number)
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
                (
                    "Cost (confirmed portion): "
                    if result.usage.unconfirmed_model_requests
                    else "Cost: "
                )
                + f"total=${result.usage.cost_usd:.8f} "
                f"uncached_input=${result.usage.input_cost_usd:.8f} "
                f"cached=${result.usage.cached_input_cost_usd:.8f} "
                f"output=${result.usage.output_cost_usd:.8f} USD"
            )
        else:
            typer.echo(
                (
                    "Cost (confirmed portion): "
                    if result.usage.unconfirmed_model_requests
                    else "Cost: "
                )
                + f"total=${result.usage.cost_usd:.8f} "
                f"input=${result.usage.input_cost_usd:.8f} "
                f"output=${result.usage.output_cost_usd:.8f} USD"
            )
        if result.usage.unconfirmed_model_requests:
            typer.echo(
                f"Unconfirmed model requests: {result.usage.unconfirmed_model_requests}; additional charges may apply."
            )
        print_patch_artifact(patch_artifact)
        return

    if result.usage.unconfirmed_model_requests:
        typer.echo(f"Confirmed cost: ${result.usage.cost_usd:.8f} USD")
        typer.echo(
            f"Unconfirmed model requests: {result.usage.unconfirmed_model_requests}; additional charges may apply."
        )
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
    outcome = result.trajectory_export
    if outcome.status == "exported":
        typer.echo(f"Trajectory: {outcome.ref.uri}")
    elif outcome.status == "failed":
        typer.echo(
            f"Trajectory export failed [{outcome.code}]: {outcome.message}"[:4096],
            err=True,
        )
    elif outcome.status == "skipped":
        typer.echo(f"Trajectory export skipped: {outcome.reason}"[:4096], err=True)
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
    if result.finalization_errors and result.run_result.status in (
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    ):
        typer.echo(
            f"Run {result.run_result.run_id} is terminal; resume cannot restart business execution or repair finalization.",
            err=True,
        )
    for error in result.finalization_errors:
        typer.echo(
            f"Finalization failed ({error.stage}, {error.code}): {error.message}"[
                :4096
            ],
            err=True,
        )


def print_resume_plan(plan: ResumePlan) -> None:
    typer.echo(f"Run: {plan.run_id}")
    typer.echo(
        f"Resume: {plan.readiness}; phase={plan.phase.value}; source sequence={plan.source_sequence}"
    )
    for diagnostic in plan.diagnostics:
        typer.echo(diagnostic)
    if plan.live_checks:
        typer.echo(f"Live checks required: {', '.join(plan.live_checks)}")
    if plan.cleanup_requirements:
        typer.echo(
            "Owned evaluation resources require verified cleanup during actual resume"
        )
    typer.echo("This inspection grants no future execution authorization.")


def print_resume_rejection(rejection: ResumeRejection) -> None:
    typer.echo("Resume: rejected", err=True)
    typer.echo(f"{rejection.code}: Run {rejection.run_id}", err=True)
    if rejection.workspace_id is not None:
        typer.echo(f"Workspace: {rejection.workspace_id}", err=True)
    if rejection.phase is not None:
        typer.echo(
            f"Phase: {rejection.phase.value}; source sequence={rejection.source_sequence}",
            err=True,
        )
    if rejection.step_number is not None:
        typer.echo(
            f"Step {rejection.step_number}, call {rejection.call_index}, tool call ID {rejection.tool_call_id}",
            err=True,
        )
    typer.echo(rejection.message[:4096], err=True)


def print_trajectory_exception(exc: BaseException) -> None:
    for note in getattr(exc, "__notes__", ()):
        if note.startswith("Trajectory"):
            typer.echo(note[:4096], err=not note.startswith("Trajectory: "))
