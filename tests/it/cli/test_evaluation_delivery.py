from miniagent.artifacts import ArtifactWrite
from miniagent.cli.application import Application
from miniagent.cli.rendering import print_application_result
from miniagent.runtime import CancellationToken


async def test_renderer_finds_patch_by_type(delivery_case, capsys):
    deps, _ = delivery_case
    (deps.workspace.root / "tracked.txt").write_text("candidate\n")
    report = await deps.artifact_store.put(
        ArtifactWrite(run_id=deps.run_id, type="evaluation_report"), b"hidden"
    )
    result = await Application(deps).run(CancellationToken())
    patch = next(ref for ref in result.artifacts if ref.type == "workspace_patch")
    result = result.model_copy(update={"artifacts": (report, patch)})
    print_application_result(result, failure_exit_code=1)
    assert f"Patch: {patch.uri}" in capsys.readouterr().out
