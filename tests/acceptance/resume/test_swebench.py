import hashlib
import json
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.environments.managed_execution import HostProcessIdentity
from miniagent.environments.process import inspect_host_execution
from miniagent.evaluation.swebench_report import parse_instance_report

from .test_local import origin, stored


def manifest_case(tmp_path):
    patch = b"diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n"
    (tmp_path / "patch.diff").write_bytes(patch)
    report = {
        "development-1": {
            "patch_exists": True,
            "patch_is_None": False,
            "patch_successfully_applied": True,
            "resolved": False,
            "tests_status": {
                "FAIL_TO_PASS": {"success": [], "failure": ["test_required"]},
                "PASS_TO_PASS": {"success": ["test_regression"], "failure": []},
            },
        }
    }
    raw = json.dumps(report).encode()
    (tmp_path / "prior-report.json").write_bytes(raw)
    return {
        "schema_version": 1,
        "selection": "development",
        "instance_id": "development-1",
        "task": "fix the public development issue",
        "data_root": str(tmp_path),
        "task_record_sha256": "a" * 64,
        "harness_version": "5.0.2",
        "harness_executable": "swebench",
        "image_id": "sha256:" + "b" * 64,
        "daemon_id": "prepared-daemon",
        "patch_path": "patch.diff",
        "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "report_path": "prior-report.json",
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "confirmed_by": "independent-fixture-owner",
        "confirmed_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "evidence_origin": "independent_prior_run",
        "prior_run_id": "external-prior-run",
        "required_failures": ["test_required"],
        "fail_to_pass": ["test_required"],
        "pass_to_pass": ["test_regression"],
    }


@pytest.mark.parametrize(
    "damage",
    [
        "missing_hash",
        "empty_patch",
        "wrong_hash",
        "wrong_scope",
        "current_run",
        "future_confirmation",
        "resolved",
        "not_applied",
        "infra_failure",
        "wrong_required_failures",
    ],
)
def test_unresolved_manifest_requires_independent_prior_evidence(tmp_path, damage):
    from .fixtures import load_unresolved_manifest

    data = manifest_case(tmp_path)
    if damage == "missing_hash":
        data.pop("patch_sha256")
    elif damage == "empty_patch":
        (tmp_path / "patch.diff").write_bytes(b"")
    elif damage == "wrong_hash":
        data["report_sha256"] = "0" * 64
    elif damage == "wrong_scope":
        data["selection"] = "evaluation"
    elif damage == "current_run":
        data["evidence_origin"] = "current_run"
    elif damage == "future_confirmation":
        data["confirmed_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif damage == "wrong_required_failures":
        data["required_failures"] = ["other_test"]
    else:
        path = tmp_path / "prior-report.json"
        report = json.loads(path.read_text())
        body = report["development-1"]
        if damage == "resolved":
            body["resolved"] = True
        elif damage == "not_applied":
            body["patch_successfully_applied"] = False
        elif damage == "infra_failure":
            body["infra_failure"] = True
        raw = json.dumps(report).encode()
        path.write_bytes(raw)
        data["report_sha256"] = hashlib.sha256(raw).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_unresolved_manifest(path, before=datetime.now(UTC))


def test_valid_prior_manifest_is_frozen_without_generating_an_oracle(tmp_path):
    from .fixtures import load_unresolved_manifest

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_case(tmp_path)))
    manifest = load_unresolved_manifest(path, before=datetime.now(UTC))
    assert manifest.required_failures == ("test_required",)
    assert (
        manifest.patch_sha256
        == hashlib.sha256((tmp_path / "patch.diff").read_bytes()).hexdigest()
    )


async def test_swebench_resume_preserves_patch_and_expected_unresolved(spawn_worker):
    """U21-04: requires an independently approved prior unresolved development manifest."""
    import asyncio

    from .fixtures import load_unresolved_manifest

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
