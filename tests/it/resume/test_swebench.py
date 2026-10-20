import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.support.resume.swebench import manifest_case


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
    from tests.support.resume.fixtures import load_unresolved_manifest

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
    from tests.support.resume.fixtures import load_unresolved_manifest

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_case(tmp_path)))
    manifest = load_unresolved_manifest(path, before=datetime.now(UTC))
    assert manifest.required_failures == ("test_required",)
    assert (
        manifest.patch_sha256
        == hashlib.sha256((tmp_path / "patch.diff").read_bytes()).hexdigest()
    )
