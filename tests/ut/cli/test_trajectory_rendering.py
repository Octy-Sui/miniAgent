from uuid import uuid4

import pytest
import typer

from miniagent.artifacts import ArtifactRef
from miniagent.cli.application import ApplicationResult, FinalizationError
from miniagent.cli.rendering import print_application_result
from miniagent.cli.trajectory import TrajectoryExported
from miniagent.runtime import RunStatus, RunSuspension, TerminationReason
from miniagent.runtime.progress import InterruptionInfo
from tests.support.cli.main import successful_result


@pytest.mark.parametrize(
    ("variant", "exit_code"),
    [
        ("success", None),
        ("failed", 1),
        ("cancelled", 1),
        ("suspended", 130),
        ("finalization_error", 1),
    ],
)
def test_trajectory_address_is_last_after_patch_and_preserves_exit_code(
    capsys, variant, exit_code
):
    terminal = successful_result()
    run_result = terminal
    if variant in ("failed", "cancelled"):
        run_result = terminal.model_copy(
            update={
                "status": RunStatus.FAILED
                if variant == "failed"
                else RunStatus.CANCELLED,
                "reason": TerminationReason.MODEL_FAILED
                if variant == "failed"
                else TerminationReason.CANCEL_REQUESTED,
                "output": None,
            }
        )
    elif variant == "suspended":
        run_result = RunSuspension(
            run_id=terminal.run_id,
            usage=terminal.usage,
            started_at=terminal.started_at,
            suspended_at=terminal.finished_at,
            interruption=InterruptionInfo(
                signal_number=2, requested_at=terminal.finished_at
            ),
        )
    patch = ArtifactRef(
        artifact_id=uuid4(), uri="file:///artifacts/patch", type="workspace_patch"
    )
    trajectory = ArtifactRef(
        artifact_id=uuid4(), uri="file:///artifacts/trajectory", type="run_trajectory"
    )
    result = ApplicationResult(
        run_result=run_result,
        artifacts=(patch,),
        trajectory_export=TrajectoryExported(ref=trajectory),
        finalization_errors=(
            FinalizationError(
                stage="workspace_cleanup",
                code="CLEANUP_FAILED",
                message="workspace retained",
            ),
        )
        if variant == "finalization_error"
        else (),
    )
    if exit_code is None:
        print_application_result(result, failure_exit_code=1)
    else:
        with pytest.raises(typer.Exit) as caught:
            print_application_result(result, failure_exit_code=1)
        assert caught.value.exit_code == exit_code
    lines = capsys.readouterr().out.splitlines()
    assert lines[-2:] == [f"Patch: {patch.uri}", f"Trajectory: {trajectory.uri}"]
