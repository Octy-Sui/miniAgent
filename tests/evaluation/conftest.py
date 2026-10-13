import hashlib
import json
import sys
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


@pytest.fixture
def docker_cli(tmp_path: Path) -> Path:
    executable = tmp_path / "docker-test"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
scenario = json.loads((root / "docker-scenario.json").read_text())
args = sys.argv[1:]
with (root / "docker-calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"argv": args, "environment": {key: value for key, value in os.environ.items() if key.startswith("DOCKER_")}}) + "\\n")
if scenario.get("hang"):
    (root / "docker-started").write_text(str(os.getpid()))
    time.sleep(30)
if args[:2] == ["context", "show"]:
    print(scenario.get("current", "a"))
elif args[:2] == ["context", "inspect"]:
    print(json.dumps([scenario["contexts"][args[2]]]))
elif "info" in args:
    print(scenario.get("daemon_id", "daemon-a"))
elif "image" in args and "inspect" in args:
    if scenario.get("image_missing"):
        print("Error response from daemon: No such image: test", file=sys.stderr)
        sys.exit(1)
    print(scenario.get("image_id", "sha256:synthetic"))
else:
    sys.exit(2)
"""
    )
    executable.chmod(0o755)
    (tmp_path / "docker-scenario.json").write_text(
        json.dumps(
            {
                "contexts": {
                    "a": {
                        "Endpoints": {
                            "docker": {
                                "Host": "unix:///private/tmp/daemon-a.sock",
                                "SkipTLSVerify": False,
                            }
                        },
                        "TLSMaterial": {},
                        "Storage": {"TLSPath": ""},
                    }
                }
            }
        )
    )
    return executable
