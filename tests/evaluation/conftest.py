import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest


@pytest.fixture
def prepared_bundle(tmp_path: Path) -> Path:
    support = json.loads(
        files("miniagent.evaluation")
        .joinpath("data/swebench-verified-25.json")
        .read_bytes()
    )
    root = tmp_path / "prepared"
    root.mkdir()
    records = [
        {
            "instance_id": task["instance_id"],
            "repo": task["repo"],
            "version": task["version"],
            "base_commit": task["base_commit"],
            "image": task["image"],
            "eval_script": "echo synthetic-tests\n",
            "log_parser": "synthetic",
            "eval_type": "pass_and_fail",
            "FAIL_TO_PASS": ["f"],
            "PASS_TO_PASS": ["p"],
        }
        for task in support["tasks"]
    ]
    content = json.dumps(records).encode()
    (root / "tasks.json").write_bytes(content)
    instance = "django__django-11477"
    report = json.dumps(
        {
            instance: {
                "patch_exists": True,
                "patch_is_None": False,
                "patch_successfully_applied": True,
                "resolved": True,
                "infra_failure": False,
                "tests_status": {
                    "FAIL_TO_PASS": {"success": ["f"], "failure": []},
                    "PASS_TO_PASS": {"success": ["p"], "failure": []},
                },
            }
        }
    ).encode()
    (root / "preflight.json").write_bytes(report)
    manifest = {
        "ready": False,
        "harness_version": "5.0.2",
        "task_base_sha256": support["task_base_sha256"],
        "sources": support["sources"],
        "validation": {
            "task_count": 25,
            "make_test_spec": "25/25 passed",
            "source_digests": "verified",
        },
        "outputs": {
            "tasks.json": {"sha256": hashlib.sha256(content).hexdigest(), "count": 25}
        },
        "tasks": [
            {
                "instance_id": task["instance_id"],
                "selection": task["selection"],
                "image": task["image"],
                "gold_preflight": (
                    {
                        "status": "passed",
                        "resolved": True,
                        "report": "preflight.json",
                        "report_sha256": hashlib.sha256(report).hexdigest(),
                    }
                    if task["instance_id"] == instance
                    else {"status": "not_run", "report": None}
                ),
            }
            for task in support["tasks"]
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root
