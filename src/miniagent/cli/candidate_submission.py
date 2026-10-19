import asyncio
import math
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from time import monotonic
from typing import Literal
from uuid import UUID, uuid4

from miniagent.artifacts import (
    ArtifactNotFoundError,
    ArtifactRef,
    ArtifactStore,
    ArtifactWrite,
)
from miniagent.cli.run_definition import CandidateSubmissionState
from miniagent.environments import Workspace
from miniagent.environments.attachment import LocalAttachment
from miniagent.environments.contracts import PatchExportCleanupError
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    execution_scope,
)
from miniagent.evaluation import (
    EvaluationError,
    EvaluationRequest,
    EvaluationResult,
    Evaluator,
)
from miniagent.evaluation.contracts import CandidateOutput
from miniagent.runtime.events import (
    CandidateSubmissionCommitted,
    CandidateSubmissionFailed,
    CandidateSubmissionStarted,
)
from miniagent.runtime.execution import EvaluationObserver, current_evaluation_observer
from miniagent.runtime.interruption import InterruptionRequested
from miniagent.runtime.progress import ComponentEnvelope
from miniagent.runtime.recording import RunPersistenceError


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    status: Literal["not_attempted", "submitted", "empty", "failed", "cancelled"] = (
        "not_attempted"
    )
    submission_id: UUID | None = None
    patch_ref: ArtifactRef | None = None
    error: EvaluationError | None = None
    diagnostics: tuple[str, ...] = ()


class CandidateArtifactInvalid(ManagedExecutionError):
    code = "CANDIDATE_ARTIFACT_INVALID"


async def validate_candidate_artifact(
    state: CandidateSubmissionState, artifact_store: ArtifactStore, run_id: UUID
) -> bytes:
    if state.status != "registered" or state.patch_ref is None:
        raise CandidateArtifactInvalid("Candidate has no registered patch")
    try:
        record = await artifact_store.get_record(state.patch_ref)
        content = await artifact_store.read(state.patch_ref)
    except (ArtifactNotFoundError, OSError, ValueError) as exc:
        raise CandidateArtifactInvalid(
            "Registered candidate patch is unavailable or changed"
        ) from exc
    if (
        record.run_id != run_id
        or record.type != "workspace_patch"
        or record.reference() != state.patch_ref
        or record.metadata.get("kind") != "candidate"
        or record.metadata.get("submission_id") != str(state.submission_id)
        or sha256(content).hexdigest() != state.patch_sha256
    ):
        raise CandidateArtifactInvalid("Registered candidate patch identity differs")
    return content


class CandidateSubmission:
    def __init__(
        self,
        workspace: Workspace,
        artifact_store: ArtifactStore,
        *,
        timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
        observer_getter: Callable[
            [], EvaluationObserver | None
        ] = current_evaluation_observer,
        diagnostic_observer_getter: Callable[[], EvaluationObserver | None]
        | None = None,
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
        self.observer_getter = observer_getter
        self.diagnostic_observer_getter = diagnostic_observer_getter
        self._results = {
            "candidate": SubmissionResult(),
            "diagnostic": SubmissionResult(),
        }
        self._lock = asyncio.Lock()

    @property
    def result(self) -> SubmissionResult:
        return self._results["candidate"]

    @property
    def diagnostic_result(self) -> SubmissionResult:
        return self._results["diagnostic"]

    @staticmethod
    def _envelope(state: CandidateSubmissionState) -> ComponentEnvelope:
        state = CandidateSubmissionState.model_validate_json(state.model_dump_json())
        return ComponentEnvelope(
            kind="candidate_submission",
            schema_version=1,
            payload=state.model_dump(mode="json"),
        )

    def _index_path(self, attempt_id: UUID) -> str:
        attachment = self.workspace.describe_attachment()
        if isinstance(attachment, LocalAttachment):
            return str(attachment.workspace_dir / f"index-{attempt_id.hex}")
        return f"/tmp/miniagent-index-{attempt_id.hex}"

    async def submit(
        self,
        *,
        kind: Literal["candidate", "diagnostic"],
        candidate_text: str | None = None,
    ) -> SubmissionResult:
        deadline = monotonic() + self.timeout_seconds
        started = False
        observer = (
            self.observer_getter()
            if kind == "candidate"
            else self.diagnostic_observer_getter()
            if self.diagnostic_observer_getter
            else None
        )
        state = None
        stage = "export"
        try:
            async with asyncio.timeout_at(deadline), self._lock:
                previous = self._results[kind]
                if previous.status == "cancelled":
                    return previous
                envelope = observer.progress.submission if observer else None
                if envelope is not None:
                    if (
                        envelope.kind != "candidate_submission"
                        or envelope.schema_version != 1
                    ):
                        raise CandidateArtifactInvalid(
                            "Unsupported candidate submission metadata"
                        )
                    try:
                        state = CandidateSubmissionState.model_validate(
                            envelope.payload
                        )
                    except ValueError as exc:
                        raise CandidateArtifactInvalid(
                            "Invalid candidate submission metadata"
                        ) from exc
                    if (
                        candidate_text is None
                        or state.candidate_sha256
                        != sha256(candidate_text.encode()).hexdigest()
                    ):
                        raise CandidateArtifactInvalid(
                            "Submission belongs to another candidate text"
                        )
                    if state.status == "registered":
                        await validate_candidate_artifact(
                            state, self.artifact_store, self.workspace.run_id
                        )
                        result = SubmissionResult(
                            status="submitted",
                            submission_id=state.submission_id,
                            patch_ref=state.patch_ref,
                        )
                        self._results[kind] = result
                        return result
                    if state.status in ("empty", "failed"):
                        result = SubmissionResult(
                            status=state.status,
                            submission_id=state.submission_id,
                            error=state.error,
                        )
                        self._results[kind] = result
                        return result
                elif previous.status != "not_attempted":
                    return previous
                if (
                    observer is not None
                    and kind == "candidate"
                    and candidate_text is None
                ):
                    raise ValueError(
                        "Persistent candidate submission requires candidate text"
                    )
                attempt_id = uuid4()
                index_path = self._index_path(attempt_id) if observer else None
                state = CandidateSubmissionState(
                    candidate_sha256=sha256(
                        (candidate_text or "").encode()
                    ).hexdigest(),
                    submission_id=state.submission_id if state else uuid4(),
                    status="exporting",
                    export_attempt_id=attempt_id,
                    temporary_index_path=index_path or "volatile",
                )
                if observer:
                    await observer.record(
                        CandidateSubmissionStarted(
                            attempt_id=attempt_id,
                            submission_id=state.submission_id,
                            candidate_sha256=state.candidate_sha256,
                            temporary_index_path=index_path or "volatile",
                        ),
                        component=self._envelope(state),
                    )
                self._results[kind] = SubmissionResult(
                    submission_id=state.submission_id
                )
                started = True
                scope = (
                    execution_scope(observer.execution_observer(attempt_id))
                    if observer
                    else nullcontext()
                )
                with scope:
                    remaining = deadline - monotonic()
                    if remaining <= self.cleanup_timeout_seconds:
                        raise TimeoutError("No export cleanup reserve remains")
                    if index_path is None:
                        patch = await self.workspace.export_patch(
                            timeout_seconds=remaining,
                            cleanup_timeout_seconds=self.cleanup_timeout_seconds,
                        )
                    else:
                        patch = await self.workspace.export_patch(
                            timeout_seconds=remaining,
                            cleanup_timeout_seconds=self.cleanup_timeout_seconds,
                            temporary_index_path=index_path,
                        )
                    if not patch.content:
                        state = state.model_copy(
                            update={
                                "status": "empty",
                                "base_revision": patch.base_revision,
                                "error": EvaluationError(
                                    code="EMPTY_PATCH",
                                    message="Candidate patch is empty",
                                ),
                            }
                        )
                    else:
                        content = patch.content.encode()
                        stage = "registration"
                        ref = await self.artifact_store.put(
                            ArtifactWrite(
                                run_id=self.workspace.run_id,
                                type="workspace_patch",
                                name="changes.patch",
                                media_type="text/x-diff",
                                metadata={
                                    "submission_id": str(state.submission_id),
                                    "kind": kind,
                                    "stage": "submission",
                                    "files": patch.files,
                                    "base_revision": patch.base_revision,
                                },
                            ),
                            content,
                        )
                        state = state.model_copy(
                            update={
                                "status": "registered",
                                "patch_ref": ref,
                                "patch_sha256": sha256(content).hexdigest(),
                                "base_revision": patch.base_revision,
                            }
                        )
                if observer:
                    await observer.record(
                        CandidateSubmissionCommitted(
                            attempt_id=attempt_id,
                            submission_id=state.submission_id,
                            candidate_sha256=state.candidate_sha256,
                            status="registered"
                            if state.status == "registered"
                            else "empty",
                            patch_ref=state.patch_ref,
                            patch_sha256=state.patch_sha256,
                            base_revision=state.base_revision,
                        ),
                        component=self._envelope(state),
                    )
                result = SubmissionResult(
                    status="submitted" if state.status == "registered" else "empty",
                    submission_id=state.submission_id,
                    patch_ref=state.patch_ref,
                    error=state.error,
                )
                self._results[kind] = result
                return result
        except (RunPersistenceError, ManagedExecutionError, InterruptionRequested):
            raise
        except asyncio.CancelledError as exc:
            if started:
                self._results[kind] = replace(
                    self._results[kind],
                    status="cancelled",
                    diagnostics=tuple(getattr(exc, "__notes__", ())),
                )
            raise
        except Exception as exc:  # noqa: BLE001 -- translate trusted export or registration failures.
            code = (
                "CANDIDATE_SUBMISSION_TIMEOUT"
                if isinstance(exc, TimeoutError)
                else "PATCH_EXPORT_CLEANUP_FAILED"
                if isinstance(exc, PatchExportCleanupError)
                else "ARTIFACT_REGISTRATION_FAILED"
                if stage == "registration"
                else "PATCH_EXPORT_FAILED"
            )
            error = EvaluationError(
                code=code, message=f"{type(exc).__name__}: {exc}"[:4096]
            )
            if observer is not None and state is not None and started:
                state = state.model_copy(
                    update={
                        "status": "failed",
                        "patch_ref": None,
                        "patch_sha256": None,
                        "error": error,
                    }
                )
                assert state.export_attempt_id is not None
                await observer.record(
                    CandidateSubmissionFailed(
                        attempt_id=state.export_attempt_id,
                        submission_id=state.submission_id,
                        candidate_sha256=state.candidate_sha256,
                        error=error,
                    ),
                    component=self._envelope(state),
                )
            result = replace(
                self._results[kind],
                status="failed",
                error=error,
                diagnostics=tuple(getattr(exc, "__notes__", ())),
            )
            if started:
                self._results[kind] = result
            return result


class WorkspaceCandidateEvaluator:
    def __init__(self, submission: CandidateSubmission, evaluator: Evaluator) -> None:
        self.submission = submission
        self.evaluator = evaluator

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        result = await self.submission.submit(
            kind="candidate", candidate_text=request.candidate.text
        )
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
