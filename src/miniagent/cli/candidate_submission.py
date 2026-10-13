import asyncio
import math
from dataclasses import dataclass, replace
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactRef, ArtifactStore, ArtifactWrite
from miniagent.environments import Workspace
from miniagent.environments.contracts import PatchExportCleanupError
from miniagent.evaluation import (
    EvaluationError,
    EvaluationRequest,
    EvaluationResult,
    Evaluator,
)
from miniagent.evaluation.contracts import CandidateOutput


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    status: Literal["not_attempted", "submitted", "empty", "failed", "cancelled"] = (
        "not_attempted"
    )
    submission_id: UUID | None = None
    patch_ref: ArtifactRef | None = None
    error: EvaluationError | None = None
    diagnostics: tuple[str, ...] = ()


class CandidateSubmission:
    def __init__(
        self,
        workspace: Workspace,
        artifact_store: ArtifactStore,
        *,
        timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
    ) -> None:
        if not (
            math.isfinite(timeout_seconds)
            and math.isfinite(cleanup_timeout_seconds)
            and 0 < cleanup_timeout_seconds < timeout_seconds
        ):
            raise ValueError("Submission timeout must exceed positive cleanup reserve")
        self.workspace = workspace
        self.artifact_store = artifact_store
        self.timeout_seconds = timeout_seconds
        self.cleanup_timeout_seconds = cleanup_timeout_seconds
        self._result = SubmissionResult()
        self._lock = asyncio.Lock()
        self._deadline: float | None = None

    @property
    def result(self) -> SubmissionResult:
        return self._result

    async def submit(
        self, *, kind: Literal["candidate", "diagnostic"]
    ) -> SubmissionResult:
        if self._result.status != "not_attempted":
            return self._result
        owner = self._result.submission_id is None
        if owner:
            self._result = replace(self._result, submission_id=uuid4())
            self._deadline = monotonic() + self.timeout_seconds
        assert self._deadline is not None
        stage = "export"
        try:
            async with asyncio.timeout_at(self._deadline):
                async with self._lock:
                    if self._result.status != "not_attempted":
                        return self._result
                    remaining = self._deadline - monotonic()
                    if remaining <= self.cleanup_timeout_seconds:
                        raise TimeoutError("No export cleanup reserve remains")
                    patch = await self.workspace.export_patch(
                        timeout_seconds=remaining,
                        cleanup_timeout_seconds=self.cleanup_timeout_seconds,
                    )
                    if not patch.content:
                        self._result = replace(
                            self._result,
                            status="empty",
                            error=EvaluationError(
                                code="EMPTY_PATCH", message="Candidate patch is empty"
                            ),
                        )
                        return self._result
                    if self._deadline - monotonic() <= 0:
                        raise TimeoutError(
                            "Submission deadline exhausted before registration"
                        )
                    content = patch.content.encode("utf-8")
                    stage = "registration"
                    ref = await self.artifact_store.put(
                        ArtifactWrite(
                            run_id=self.workspace.run_id,
                            type="workspace_patch",
                            name="changes.patch",
                            media_type="text/x-diff",
                            metadata={
                                "submission_id": str(self._result.submission_id),
                                "kind": kind,
                                "stage": "submission",
                                "files": patch.files,
                                "base_revision": patch.base_revision,
                            },
                        ),
                        content,
                    )
                    self._result = replace(
                        self._result, status="submitted", patch_ref=ref
                    )
                    return self._result
        except asyncio.CancelledError as exc:
            if owner:
                self._result = replace(
                    self._result,
                    status="cancelled",
                    diagnostics=tuple(getattr(exc, "__notes__", ())),
                )
            raise
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, TimeoutError):
                code = "CANDIDATE_SUBMISSION_TIMEOUT"
            elif isinstance(exc, PatchExportCleanupError):
                code = "PATCH_EXPORT_CLEANUP_FAILED"
            else:
                code = (
                    "ARTIFACT_REGISTRATION_FAILED"
                    if stage == "registration"
                    else "PATCH_EXPORT_FAILED"
                )
            failure = replace(
                self._result,
                status="failed",
                error=EvaluationError(
                    code=code, message=f"{type(exc).__name__}: {exc}"[:4096]
                ),
                diagnostics=tuple(getattr(exc, "__notes__", ())),
            )
            if owner:
                self._result = failure
            return failure


class WorkspaceCandidateEvaluator:
    def __init__(self, submission: CandidateSubmission, evaluator: Evaluator) -> None:
        self.submission = submission
        self.evaluator = evaluator

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        result = await self.submission.submit(kind="candidate")
        if result.status != "submitted":
            return EvaluationResult(
                passed=False,
                error=result.error
                or EvaluationError(
                    code="PATCH_EXPORT_FAILED",
                    message="Candidate submission is unavailable",
                ),
            )
        return await self.evaluator.evaluate(
            EvaluationRequest(
                run_id=request.run_id,
                task=request.task,
                plan=request.plan,
                candidate=CandidateOutput(
                    text=request.candidate.text, patch=result.patch_ref
                ),
            )
        )
