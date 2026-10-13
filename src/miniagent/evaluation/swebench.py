import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactRef, ArtifactStore
from miniagent.evaluation import EvaluationRequest
from miniagent.evaluation.swebench_data import PreparedTask


class EvaluationInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class EvaluationAttempt:
    attempt_id: UUID
    directory: Path
    harness_run_id: str
    model_key: str
    instance_id: str
    patch_ref: ArtifactRef
    patch_bytes: bytes
    submission_id: str
    predictions_sha256: str

    @property
    def instance_directory(self) -> Path:
        return (
            self.directory
            / "logs/run_evaluation"
            / self.harness_run_id
            / self.model_key
            / self.instance_id
        )


def _attempt_path(attempt: EvaluationAttempt, relative: str) -> Path:
    path = (attempt.directory / relative).resolve()
    if not path.is_relative_to(attempt.directory):
        raise EvaluationInputError(
            "REPORT_MISMATCH", "Evidence path escapes attempt directory"
        )
    return path


async def prepare_attempt(
    request: EvaluationRequest,
    *,
    prepared: PreparedTask,
    artifact_store: ArtifactStore,
    work_root: Path,
    model_name: str,
    timeout_seconds: float = 30.0,
) -> EvaluationAttempt:
    stage = "patch"
    try:
        async with asyncio.timeout(timeout_seconds):
            ref = request.candidate.patch
            if ref is None:
                raise EvaluationInputError(
                    "PATCH_ARTIFACT_INVALID", "No submitted patch"
                )
            record = await artifact_store.get_record(ref)
            if record.run_id != request.run_id or record.type != "workspace_patch":
                raise EvaluationInputError(
                    "PATCH_ARTIFACT_INVALID",
                    "Patch belongs to another run or artifact type",
                )
            submission_id = record.metadata.get("submission_id")
            if not isinstance(submission_id, str):
                raise EvaluationInputError(
                    "PATCH_ARTIFACT_INVALID", "Patch lacks submission identity"
                )
            UUID(submission_id)
            patch_bytes = await artifact_store.read(ref)
            if not patch_bytes:
                raise EvaluationInputError("EMPTY_PATCH", "Empty submitted patch")
            patch = patch_bytes.decode("utf-8")
            stage = "inputs"
            attempt_id = uuid4()
            directory = (
                work_root.expanduser().resolve() / request.run_id.hex / attempt_id.hex
            )
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=False)
            model_key = (
                "model-" + hashlib.sha256(model_name.encode("utf-8")).hexdigest()[:16]
            )
            prediction = (
                json.dumps(
                    {
                        "instance_id": prepared.record.instance_id,
                        "model_name_or_path": model_key,
                        "model_patch": patch,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n"
            )
            attempt = EvaluationAttempt(
                attempt_id,
                directory,
                f"{request.run_id.hex}-{attempt_id.hex}",
                model_key,
                prepared.record.instance_id,
                ref,
                patch_bytes,
                submission_id,
                hashlib.sha256(prediction).hexdigest(),
            )
            task = prepared.record.model_dump(mode="json", by_alias=True)
            task["image"] = prepared.actual_image
            config = {
                "run_id": str(request.run_id),
                "attempt_id": str(attempt_id),
                "instance_id": attempt.instance_id,
                "submission_id": submission_id,
                "model_name": model_name,
                "model_key": model_key,
                "patch_ref": ref.model_dump(mode="json"),
                "patch_artifact_id": str(ref.artifact_id),
                "patch_sha256": record.sha256,
                "predictions_sha256": attempt.predictions_sha256,
                "manifest_sha256": prepared.manifest_sha256,
                "tasks_sha256": prepared.tasks_sha256,
                "record_sha256": prepared.record_sha256,
                "harness_version": prepared.harness_version,
                "default_image": prepared.default_image,
                "actual_image": prepared.actual_image,
                "image_preflight_verified": prepared.image_preflight_verified,
                "base_revision": record.metadata.get("base_revision"),
            }
            for name, content in (
                ("task.json", json.dumps([task]).encode()),
                ("predictions.jsonl", prediction),
                ("evaluation-config.json", json.dumps(config).encode()),
            ):
                await asyncio.to_thread(
                    _attempt_path(attempt, name).write_bytes, content
                )
            return attempt
    except EvaluationInputError:
        raise
    except Exception as exc:
        code = (
            "PATCH_ARTIFACT_INVALID"
            if stage == "patch"
            else "EVALUATION_ARTIFACT_FAILED"
        )
        raise EvaluationInputError(code, f"{type(exc).__name__}: {exc}") from exc
