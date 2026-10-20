import hashlib
import json
from datetime import UTC, datetime, timedelta


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
