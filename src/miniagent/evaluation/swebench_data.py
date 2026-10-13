import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
)

from miniagent.evaluation.swebench_report import (
    parse_instance_report,
)

NonemptyString = Annotated[str, Field(min_length=1, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]


class EvaluationPreparationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class SWEbenchTaskContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: NonemptyString
    evaluation_image: NonemptyString | None = None


class SWEbenchRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: NonemptyString
    repo: NonemptyString
    version: NonemptyString
    base_commit: NonemptyString
    image: NonemptyString
    eval_script: NonemptyString
    log_parser: NonemptyString
    eval_type: Literal["pass_and_fail", "fail_only"]
    fail_to_pass: tuple[str, ...] = Field(alias="FAIL_TO_PASS")
    pass_to_pass: tuple[str, ...] = Field(alias="PASS_TO_PASS")

    @field_validator("fail_to_pass", "pass_to_pass", mode="before")
    @classmethod
    def normalize_tests(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ValueError("Tests must be a JSON list of strings")
        if len(set(value)) != len(value):
            raise ValueError("Duplicate required tests")
        return tuple(value)


class _Source(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    dataset: str
    revision: str
    file: str
    sha256: Digest


class _Output(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    sha256: Digest
    count: int = Field(ge=0)


class _Preflight(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    status: Literal["passed", "failed", "cancelled", "not_run"]
    resolved: StrictBool | None = None
    report: str | None = None
    report_sha256: Digest | None = None


class _ManifestTask(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    instance_id: str
    selection: Literal["development", "evaluation"]
    image: str
    gold_preflight: _Preflight


class _Validation(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    task_count: Literal[25]
    make_test_spec: Literal["25/25 passed"]
    source_digests: Literal["verified"]


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    schema_version: Literal[1] | None = None
    ready: StrictBool
    harness_version: str
    task_base_sha256: Digest
    sources: list[_Source]
    validation: _Validation
    outputs: dict[str, _Output]
    tasks: list[_ManifestTask]


@dataclass(frozen=True, slots=True)
class PreparedTask:
    record: SWEbenchRecord
    manifest_sha256: str
    tasks_sha256: str
    record_sha256: str
    harness_version: str
    default_image: str
    actual_image: str
    image_preflight_verified: bool


def load_support_list() -> dict:
    return json.loads(
        files("miniagent.evaluation")
        .joinpath("data/swebench-verified-25.json")
        .read_bytes()
    )


def load_prepared_task(data_root: Path, context: SWEbenchTaskContext) -> PreparedTask:
    support = load_support_list()
    supported = {task["instance_id"]: task for task in support["tasks"]}
    if context.instance_id not in supported:
        raise EvaluationPreparationError("TASK_UNSUPPORTED", context.instance_id)
    root = data_root.expanduser().resolve()
    try:
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = _Manifest.model_validate_json(manifest_bytes)
        if manifest.harness_version != "5.0.2":
            raise EvaluationPreparationError(
                "HARNESS_VERSION_MISMATCH", manifest.harness_version
            )
        if manifest.task_base_sha256 != support["task_base_sha256"]:
            raise ValueError("Support list source digest differs")
        sources = [
            {
                key: getattr(source, key)
                for key in ("dataset", "revision", "file", "sha256")
            }
            for source in manifest.sources
        ]
        if sources != support["sources"]:
            raise ValueError("Frozen sources differ")
        if len(manifest.tasks) != 25 or {
            task.instance_id for task in manifest.tasks
        } != set(supported):
            raise ValueError("Manifest task membership differs")
        for task in manifest.tasks:
            expected = supported[task.instance_id]
            if (
                task.selection != expected["selection"]
                or task.image != expected["image"]
            ):
                raise ValueError("Manifest group or image differs")
        task_bytes = (root / "tasks.json").read_bytes()
        output = manifest.outputs["tasks.json"]
        if (
            output.count != 25
            or hashlib.sha256(task_bytes).hexdigest() != output.sha256
        ):
            raise ValueError("Task data digest or count differs")
        raw_records = json.loads(task_bytes)
        if not isinstance(raw_records, list):
            raise TypeError("Task data must be a list")
        records = [SWEbenchRecord.model_validate(raw) for raw in raw_records]
        if len(records) != 25 or {record.instance_id for record in records} != set(
            supported
        ):
            raise ValueError("Task data membership differs")
        for record in records:
            expected = supported[record.instance_id]
            if any(
                getattr(record, key) != expected[key]
                for key in ("repo", "version", "base_commit", "image")
            ):
                raise ValueError("Task baseline or image differs")
        selected = next(
            task for task in manifest.tasks if task.instance_id == context.instance_id
        )
        preflight = selected.gold_preflight
        if preflight.status != "passed" or preflight.resolved is not True:
            raise EvaluationPreparationError("TASK_NOT_READY", context.instance_id)
        if not preflight.report or not preflight.report_sha256:
            raise ValueError("Selected preflight lacks report evidence")
        report_path = (root / preflight.report).resolve()
        if not report_path.is_relative_to(root):
            raise ValueError("Preflight report escapes data root")
        report_bytes = report_path.read_bytes()
        if hashlib.sha256(report_bytes).hexdigest() != preflight.report_sha256:
            raise ValueError("Preflight report digest differs")
        record = next(
            record for record in records if record.instance_id == context.instance_id
        )
        if not parse_instance_report(
            report_bytes,
            instance_id=context.instance_id,
            fail_to_pass=list(record.fail_to_pass),
            pass_to_pass=list(record.pass_to_pass),
        ).resolved:
            raise ValueError("Preflight report is unresolved")
        return PreparedTask(
            record=record,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            tasks_sha256=output.sha256,
            record_sha256=hashlib.sha256(
                record.model_dump_json(by_alias=True).encode()
            ).hexdigest(),
            harness_version=manifest.harness_version,
            default_image=record.image,
            actual_image=context.evaluation_image or record.image,
            image_preflight_verified=context.evaluation_image is None,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, EvaluationPreparationError):
            raise
        detail = (
            "Structured data validation failed: "
            + ", ".join(sorted({item["type"] for item in exc.errors()}))
            if isinstance(exc, ValidationError)
            else type(exc).__name__
        )
        raise EvaluationPreparationError(
            "EVALUATION_DATA_INVALID", f"{context.instance_id} at {root}: {detail}"
        ) from exc
