import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.managed_execution import HostProcessIdentity
from miniagent.environments.process import inspect_host_execution
from miniagent.evaluation.swebench_report import parse_instance_report
from tests.support.resume.local import origin, stored


async def test_swebench_resume_preserves_patch_and_expected_unresolved(spawn_worker):
    """U21-04: requires an independently approved prior unresolved development manifest."""
    import asyncio

    from tests.support.resume.fixtures import load_unresolved_manifest

    path = os.environ.get("MINIAGENT_RESUME_UNRESOLVED_MANIFEST")
    if not path:
        pytest.skip(
            "DEFER: no independent prior unresolved manifest; user confirmed unavailable"
        )
    manifest = load_unresolved_manifest(Path(path), before=datetime.now(UTC))
    worker = await spawn_worker(unresolved_manifest=path, gate_harness=True)
    gate = await worker.boundary("harness", timeout=120)
    record, events = await stored(worker.root)
    assert record.last_event_sequence == gate["sequence"]
    attempt = record.state.execution.evaluation.attempt
    assert attempt is not None
    old_attempt = attempt.payload["attempt_id"]
    patch_ref = next(
        e.payload.patch_ref
        for e in reversed(events)
        if e.type.value == "evaluation_attempt_started"
    )
    assert patch_ref is not None and patch_ref.sha256 == manifest.patch_sha256
    artifacts = FileArtifactStore(worker.root / "artifacts")
    frozen_patch = await artifacts.read(patch_ref)
    assert frozen_patch == manifest.patch_path.read_bytes()
    old_operation = record.state.execution.active_operation
    assert old_operation is not None
    worker.process.kill()
    assert (await worker.finish())[0] == -signal.SIGKILL
    # Wait for the owned harness naturally; never infer group end from leader exit.
    async with asyncio.timeout(2400):
        while any(
            inspect_host_execution(i).outcome != "ended"
            for i in old_operation.executions
            if isinstance(i, HostProcessIdentity)
        ):
            await asyncio.sleep(0.1)
    resumed = await spawn_worker(
        "resume",
        label="swebench-resumed",
        run_id=origin(worker.root)["run_id"],
        unresolved_manifest=path,
    )
    code, output = await resumed.finish(timeout=2500)
    assert code == 1, output
    result = json.loads((worker.root / "result-swebench-resumed.json").read_text())[
        "run_result"
    ]
    assert (result["status"], result["reason"], result["error"]["code"]) == (
        "failed",
        "evaluation_failed",
        "TASK_UNRESOLVED",
    )
    final_record, events = await stored(worker.root)
    attempts = [
        e.payload.attempt_id
        for e in events
        if e.type.value == "evaluation_attempt_started"
    ]
    assert (
        len(attempts) >= 2
        and str(attempts[0]) == old_attempt
        and attempts[0] != attempts[1]
    )
    assert await artifacts.read(patch_ref) == frozen_patch
    evaluation = final_record.state.execution.evaluation.result
    assert evaluation is not None
    report_refs = [
        ref for ref in evaluation.artifacts if ref.type == "evaluation_report"
    ]
    assert report_refs

    report = parse_instance_report(
        await artifacts.read(report_refs[-1]),
        instance_id=manifest.instance_id,
        fail_to_pass=list(manifest.fail_to_pass),
        pass_to_pass=list(manifest.pass_to_pass),
    )
    assert (
        report.patch_successfully_applied
        and not report.resolved
        and not report.infra_failure
    )
    assert report.tests_status is not None
    failures = (
        report.tests_status.FAIL_TO_PASS.failure
        + report.tests_status.PASS_TO_PASS.failure
    )
    assert set(failures) == set(manifest.required_failures)
