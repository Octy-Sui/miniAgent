import hashlib
import json

import pytest

from miniagent.evaluation.swebench_data import (
    EvaluationPreparationError,
    SWEbenchTaskContext,
    load_prepared_task,
)
from tests.support.evaluation.swebench_data import INSTANCE


def test_selected_ready_task_does_not_require_global_ready(prepared_bundle):
    prepared = load_prepared_task(
        prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE)
    )
    assert prepared.record.instance_id == INSTANCE
    assert prepared.harness_version == "5.0.2"
    assert prepared.image_preflight_verified is True
    assert prepared.record.fail_to_pass == ("f",)


def test_unsupported_task_is_rejected_before_data_read(tmp_path):
    with pytest.raises(EvaluationPreparationError, match="TASK_UNSUPPORTED"):
        load_prepared_task(tmp_path, SWEbenchTaskContext(instance_id="other"))


@pytest.mark.parametrize(("status",), [("not_run",), ("failed",), ("cancelled",)])
def test_unready_selected_task_is_rejected(prepared_bundle, status):
    path = prepared_bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    next(task for task in manifest["tasks"] if task["instance_id"] == INSTANCE)[
        "gold_preflight"
    ]["status"] = status
    path.write_text(json.dumps(manifest))
    with pytest.raises(EvaluationPreparationError, match="TASK_NOT_READY"):
        load_prepared_task(prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE))


@pytest.mark.parametrize(
    ("mutation",),
    [
        ("duplicate",),
        ("missing",),
        ("extra",),
        ("group",),
        ("list_digest",),
        ("source_revision",),
        ("source_hash",),
        ("report_hash",),
        ("version",),
    ],
)
def test_invalid_manifest_is_rejected(prepared_bundle, mutation):
    path = prepared_bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if mutation == "duplicate":
        manifest["tasks"][1] = manifest["tasks"][0]
    elif mutation == "missing":
        manifest["tasks"].pop()
    elif mutation == "extra":
        manifest["tasks"][0]["instance_id"] = "other"
    elif mutation == "group":
        manifest["tasks"][0]["selection"] = "evaluation"
    elif mutation == "list_digest":
        manifest["task_base_sha256"] = "0" * 64
    elif mutation.startswith("source_"):
        manifest["sources"][0][
            "revision" if mutation == "source_revision" else "sha256"
        ] = "0" * (40 if mutation == "source_revision" else 64)
    elif mutation == "report_hash":
        next(task for task in manifest["tasks"] if task["instance_id"] == INSTANCE)[
            "gold_preflight"
        ]["report_sha256"] = "0" * 64
    elif mutation == "version":
        manifest["harness_version"] = "5.0.3"
    path.write_text(json.dumps(manifest))
    with pytest.raises(
        EvaluationPreparationError,
        match="EVALUATION_DATA_INVALID|HARNESS_VERSION_MISMATCH",
    ):
        load_prepared_task(prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("patch", "gold"),
        ("test_patch", "hidden"),
        ("eval_script", ""),
        ("FAIL_TO_PASS", "not json"),
        ("PASS_TO_PASS", 1),
    ],
)
def test_invalid_record_is_rejected_even_with_matching_digest(
    prepared_bundle, field, value
):
    path = prepared_bundle / "tasks.json"
    records = json.loads(path.read_bytes())
    records[0][field] = value
    content = json.dumps(records).encode()
    path.write_bytes(content)
    manifest_path = prepared_bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["outputs"]["tasks.json"]["sha256"] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(
        EvaluationPreparationError, match="EVALUATION_DATA_INVALID"
    ) as caught:
        load_prepared_task(prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE))
    if field in {"patch", "test_patch"}:
        assert value not in str(caught.value)


def test_tasks_digest_is_checked(prepared_bundle):
    (prepared_bundle / "tasks.json").write_text("[]")
    with pytest.raises(EvaluationPreparationError, match="EVALUATION_DATA_INVALID"):
        load_prepared_task(prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE))


def test_report_symlink_cannot_escape_data_root(prepared_bundle, tmp_path):
    path = prepared_bundle / "preflight.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(EvaluationPreparationError, match="EVALUATION_DATA_INVALID"):
        load_prepared_task(prepared_bundle, SWEbenchTaskContext(instance_id=INSTANCE))


def test_override_does_not_change_shared_data_and_snapshot(prepared_bundle):
    path = prepared_bundle / "tasks.json"
    before = path.read_bytes()
    prepared = load_prepared_task(
        prepared_bundle,
        SWEbenchTaskContext(instance_id=INSTANCE, evaluation_image="local:custom"),
    )
    assert prepared.actual_image == "local:custom"
    assert prepared.image_preflight_verified is False
    assert path.read_bytes() == before
    path.write_text("[]")
    assert prepared.record.fail_to_pass == ("f",)
