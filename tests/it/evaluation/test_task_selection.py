import hashlib
import json
from pathlib import Path

import pytest

from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
    load_prepared_task,
    load_support_list,
)

INSTANCE = "matplotlib__matplotlib-22871"
IMAGE = (
    "docker.io/swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-22871"
    "@sha256:0b50468d5fab27e15ed1d19f001d357534872b4392b71683e400923881c258aa"
)


def test_replacement_task_loads_its_own_validated_preflight(prepared_bundle: Path):
    manifest_path = prepared_bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    candidates = {INSTANCE, "psf__requests-2931"}
    task = next(t for t in manifest["tasks"] if t["instance_id"] in candidates)
    task.update(instance_id=INSTANCE, image=IMAGE)
    report = json.loads((prepared_bundle / "preflight.json").read_text())
    report_bytes = json.dumps({INSTANCE: next(iter(report.values()))}).encode()
    (prepared_bundle / "replacement-preflight.json").write_bytes(report_bytes)
    task["gold_preflight"] = {
        "status": "passed",
        "resolved": True,
        "report": "replacement-preflight.json",
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
    }
    records = json.loads((prepared_bundle / "tasks.json").read_text())
    record = next(r for r in records if r["instance_id"] in candidates)
    record.update(
        instance_id=INSTANCE,
        image=IMAGE,
        repo="matplotlib/matplotlib",
        version="3.5",
        base_commit="a7b7260bf06c20d408215d95ce20a1a01c12e5b1",
    )
    content = json.dumps(records).encode()
    (prepared_bundle / "tasks.json").write_bytes(content)
    manifest["outputs"]["tasks.json"]["sha256"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    prepared = load_prepared_task(
        prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE)
    )

    assert prepared.record.instance_id == INSTANCE
    assert prepared.actual_image == IMAGE
    assert prepared.record.fail_to_pass == ("f",)


def test_replaced_task_is_rejected_before_loading_old_data(tmp_path: Path):
    with pytest.raises(EvaluationPreparationError, match="TASK_UNSUPPORTED"):
        load_prepared_task(
            tmp_path, SWEbenchTaskContext(instance_id="psf__requests-2931")
        )


def test_agent_selection_and_evaluator_support_are_the_same_snapshot():
    path = Path(__file__).resolve().parents[3] / "docs/swebench-task-base.json"
    content = path.read_bytes()
    support = load_support_list()
    assert support["task_base_sha256"] == hashlib.sha256(content).hexdigest()
    agent_tasks = json.loads(content)["tasks"]
    assert len(support["tasks"]) == len(agent_tasks) == 25
    assert [
        {key: task[key] for key in support["tasks"][0]} for task in agent_tasks
    ] == support["tasks"]
