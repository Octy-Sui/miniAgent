import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from miniagent.evaluation.swebench_report import parse_instance_report

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[str, Field(min_length=1)]


class UnresolvedManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    selection: Literal["development"]
    instance_id: Nonempty
    task: Nonempty
    data_root: Path
    task_record_sha256: Digest
    harness_version: Literal["5.0.2"]
    harness_executable: Nonempty
    image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    daemon_id: Nonempty
    patch_path: Path
    patch_sha256: Digest
    report_path: Path
    report_sha256: Digest
    confirmed_by: Nonempty
    confirmed_at: AwareDatetime
    evidence_origin: Literal["independent_prior_run"]
    prior_run_id: Nonempty
    required_failures: tuple[Nonempty, ...] = Field(min_length=1)
    fail_to_pass: tuple[Nonempty, ...] = Field(min_length=1)
    pass_to_pass: tuple[Nonempty, ...]

    @model_validator(mode="after")
    def validate_test_sets(self):
        for tests in (self.required_failures, self.fail_to_pass, self.pass_to_pass):
            if len(tests) != len(set(tests)):
                raise ValueError("Duplicate test names")
        if set(self.fail_to_pass) & set(self.pass_to_pass):
            raise ValueError("Required test groups overlap")
        if not set(self.required_failures) <= set(
            self.fail_to_pass + self.pass_to_pass
        ):
            raise ValueError("Failures are outside the prepared task")
        return self


def load_unresolved_manifest(path: Path, *, before: datetime) -> UnresolvedManifest:
    manifest = UnresolvedManifest.model_validate_json(path.read_bytes())
    if manifest.confirmed_at >= before:
        raise ValueError("Independent confirmation must predate this acceptance run")
    paths = {}
    for field in ("patch_path", "report_path", "data_root"):
        value = getattr(manifest, field)
        paths[field] = (path.parent / value).resolve()
    manifest = manifest.model_copy(update=paths)
    patch = manifest.patch_path.read_bytes()
    if not patch.strip() or hashlib.sha256(patch).hexdigest() != manifest.patch_sha256:
        raise ValueError("Nonempty patch bytes and matching hash are required")
    raw = manifest.report_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.report_sha256:
        raise ValueError("Prior report hash differs")
    report = parse_instance_report(
        raw,
        instance_id=manifest.instance_id,
        fail_to_pass=list(manifest.fail_to_pass),
        pass_to_pass=list(manifest.pass_to_pass),
    )
    if (
        not report.patch_exists
        or report.patch_is_None
        or not report.patch_successfully_applied
        or report.resolved
        or report.infra_failure
        or report.tests_status is None
    ):
        raise ValueError("Prior report must prove applied, valid unresolved")
    failures = (
        report.tests_status.FAIL_TO_PASS.failure
        + report.tests_status.PASS_TO_PASS.failure
    )
    if set(failures) != set(manifest.required_failures):
        raise ValueError("Prior report required failures differ")
    return manifest


if __name__ == "__main__":
    destination = (
        Path(__file__).with_name("fixtures") / "unresolved-manifest.schema.json"
    )
    destination.write_text(
        json.dumps(UnresolvedManifest.model_json_schema(), indent=2) + "\n"
    )
