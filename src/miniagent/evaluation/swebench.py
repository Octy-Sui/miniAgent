import asyncio
import hashlib
import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactRef, ArtifactStore, ArtifactWrite
from miniagent.cli.settings import Settings
from miniagent.environments.managed_execution import (
    HostProcessIdentity,
    ManagedExecutionError,
    current_cleanup_deadline,
    current_execution_observer,
    execution_scope,
)
from miniagent.environments.process import (
    inspect_host_execution,
    reap_process,
    start_host_process,
)
from miniagent.evaluation import EvaluationError, EvaluationRequest, EvaluationResult
from miniagent.evaluation.swebench_data import EvaluationPreparationError, PreparedTask
from miniagent.evaluation.swebench_docker import (
    EvaluationAttemptBusy,
    EvaluationDockerConnection,
    SWEbenchAttemptState,
    check_daemon,
    cleanup_containers,
    harness_environment,
    inspect_image,
)
from miniagent.evaluation.swebench_report import (
    ReportValidationError,
    parse_instance_report,
)
from miniagent.runtime.events import (
    EvaluationAttemptInterrupted,
    EvaluationAttemptStarted,
)
from miniagent.runtime.execution import EvaluationObserver, current_evaluation_observer
from miniagent.runtime.interruption import InterruptionRequested
from miniagent.runtime.progress import ComponentEnvelope
from miniagent.runtime.recording import RunPersistenceError


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
    attempt_id: UUID | None = None,
    observer: EvaluationObserver | None = None,
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
            attempt_id = attempt_id or uuid4()
            directory = (
                work_root.expanduser().resolve() / request.run_id.hex / attempt_id.hex
            )
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
            if observer is not None:
                state = SWEbenchAttemptState(
                    attempt_id=attempt_id,
                    harness_run_id=attempt.harness_run_id,
                    instance_id=attempt.instance_id,
                    task_digest=prepared.record_sha256,
                    patch_ref=ref,
                    patch_sha256=record.sha256,
                    directory=directory,
                    resource_instance_id=attempt.instance_id,
                    resource_harness_run_id=attempt.harness_run_id,
                )
                await observer.record(
                    EvaluationAttemptStarted(
                        attempt_id=attempt_id,
                        harness_run_id=attempt.harness_run_id,
                        instance_id=attempt.instance_id,
                        task_digest=prepared.record_sha256,
                        patch_ref=ref,
                        patch_sha256=record.sha256,
                        directory=str(directory),
                    ),
                    component=ComponentEnvelope(
                        kind="swebench_attempt",
                        schema_version=1,
                        payload=state.model_dump(mode="json"),
                    ),
                )
            await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=False)
            for name, content in (
                ("task.json", json.dumps([task]).encode()),
                ("predictions.jsonl", prediction),
                ("evaluation-config.json", json.dumps(config).encode()),
            ):
                await asyncio.to_thread(
                    _attempt_path(attempt, name).write_bytes, content
                )
            return attempt
    except (
        EvaluationInputError,
        ManagedExecutionError,
        RunPersistenceError,
        InterruptionRequested,
    ):
        raise
    except Exception as exc:
        code = (
            "PATCH_ARTIFACT_INVALID"
            if stage == "patch"
            else "EVALUATION_ARTIFACT_FAILED"
        )
        raise EvaluationInputError(code, f"{type(exc).__name__}: {exc}") from exc


async def _stop_harness(
    process: asyncio.subprocess.Process | None, deadline: float
) -> list[str]:
    if process is None:
        return []
    diagnostics = list(await reap_process(process, [], deadline=deadline))
    return diagnostics


class SWEbenchEvaluator:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        prepared: PreparedTask,
        settings: Settings,
        model_name: str,
        executable: Path,
        docker_executable: Path,
        connection: EvaluationDockerConnection,
        environment: dict[str, str],
        observer_getter: Callable[
            [], EvaluationObserver | None
        ] = current_evaluation_observer,
    ) -> None:
        self.observer_getter = observer_getter
        self.artifact_store = artifact_store
        self.prepared = prepared
        self.settings = settings.model_copy(deep=True)
        self.model_name = model_name
        self.executable = executable
        self.docker_executable = docker_executable
        self.connection = connection
        self.environment = harness_environment(environment.copy(), connection)

    async def _judge(
        self, attempt: EvaluationAttempt, exit_code: int
    ) -> EvaluationError | None:
        if exit_code:
            return EvaluationError(
                code="HARNESS_PROCESS_FAILED",
                message=f"Harness exited with code {exit_code}",
            )
        try:
            prefix = str(attempt.instance_directory.relative_to(attempt.directory))
            report_path = _attempt_path(attempt, f"{prefix}/report.json")
            if not report_path.is_file():
                output = _attempt_path(attempt, f"{prefix}/test_output.txt")
                marker = f"\n\nTimeout error: {self.settings.swebench_test_timeout_seconds} seconds exceeded.".encode()
                if output.is_file() and (
                    await asyncio.to_thread(output.read_bytes)
                ).endswith(marker):
                    return EvaluationError(
                        code="EVALUATION_TEST_TIMEOUT",
                        message="Harness test timeout marker observed",
                    )
                return EvaluationError(
                    code="REPORT_MISSING", message="Instance report is missing"
                )
            patch_path = _attempt_path(attempt, f"{prefix}/patch.diff")
            if (
                not patch_path.is_file()
                or await asyncio.to_thread(patch_path.read_bytes) != attempt.patch_bytes
            ):
                return EvaluationError(
                    code="REPORT_MISMATCH",
                    message="Harness patch differs from submission",
                )
            prediction = await asyncio.to_thread(
                _attempt_path(attempt, "predictions.jsonl").read_bytes
            )
            if hashlib.sha256(prediction).hexdigest() != attempt.predictions_sha256:
                return EvaluationError(
                    code="REPORT_MISMATCH",
                    message="Predictions changed during evaluation",
                )
            report = parse_instance_report(
                await asyncio.to_thread(report_path.read_bytes),
                instance_id=attempt.instance_id,
                fail_to_pass=list(self.prepared.record.fail_to_pass),
                pass_to_pass=list(self.prepared.record.pass_to_pass),
            )
            if not report.resolved:
                return EvaluationError(
                    code="TASK_UNRESOLVED", message="Candidate did not resolve the task"
                )
        except (EvaluationInputError, ReportValidationError) as exc:
            return EvaluationError(code=exc.code, message=str(exc)[:4096])
        except OSError as exc:
            return EvaluationError(
                code="REPORT_INVALID",
                message=f"Cannot read report evidence: {exc}"[:4096],
            )
        return None

    async def _cleanup(
        self,
        attempt: EvaluationAttempt,
        process: asyncio.subprocess.Process | None,
        identity: HostProcessIdentity | None = None,
    ) -> tuple[list[dict[str, str]], list[str]]:
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.swebench_cleanup_timeout_seconds
        )
        deadline = min(deadline, current_cleanup_deadline() or float("inf"))
        diagnostics = await _stop_harness(process, deadline)
        if diagnostics:
            return [], diagnostics
        observer = current_execution_observer()
        if observer is not None and identity is not None:
            await observer.ended(inspect_host_execution(identity))
        identities, container_diagnostics = await cleanup_containers(
            self.docker_executable,
            self.connection,
            instance_id=attempt.instance_id,
            harness_run_id=attempt.harness_run_id,
            deadline=deadline,
        )
        return identities, [*diagnostics, *container_diagnostics]

    async def _collect(
        self,
        request: EvaluationRequest,
        attempt: EvaluationAttempt,
        error: EvaluationError | None,
        diagnostics: list[str],
        cancelled: asyncio.Event,
        config: dict,
    ) -> tuple[list[ArtifactRef], EvaluationError | None]:
        refs = [attempt.patch_ref]
        prefix = str(attempt.instance_directory.relative_to(attempt.directory))
        paths = [
            ("evaluation_predictions", "predictions.jsonl"),
            ("evaluation_config", "evaluation-config.json"),
            ("evaluation_stdout", "stdout.log"),
            ("evaluation_stderr", "stderr.log"),
            ("evaluation_report", f"{prefix}/report.json"),
            ("evaluation_test_output", f"{prefix}/test_output.txt"),
            ("evaluation_instance_log", f"{prefix}/run_instance.log"),
        ]
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.swebench_artifact_timeout_seconds
        )
        deadline = min(deadline, current_cleanup_deadline() or float("inf"))
        unregistered: list[str] = []

        async def publish(kind: str, name: str, content: bytes) -> ArtifactRef:
            return await self.artifact_store.put(
                ArtifactWrite(
                    run_id=request.run_id,
                    type=kind,
                    name=Path(name).name,
                    media_type="application/json"
                    if kind
                    in {"evaluation_report", "evaluation_config", "evaluation_summary"}
                    else "text/plain",
                    metadata={
                        "attempt_id": str(attempt.attempt_id),
                        "instance_id": attempt.instance_id,
                        "stage": "evaluation",
                    },
                ),
                content,
            )

        def summary_bytes(*, collecting: bool = False) -> bytes:
            return json.dumps(
                {
                    "scope": "evaluation_evidence_snapshot",
                    "terminal_status_source": "Run",
                    "verdict": "cancelled"
                    if cancelled.is_set()
                    else "failed"
                    if error
                    else "collecting"
                    if collecting
                    else "report_resolved",
                    "error": error.model_dump() if error else None,
                    "message": error.message[:4096] if error else "",
                    "run_id": str(request.run_id),
                    "attempt_id": str(attempt.attempt_id),
                    "instance_id": attempt.instance_id,
                    "submission_id": attempt.submission_id,
                    "patch_artifact_id": str(attempt.patch_ref.artifact_id),
                    "patch_sha256": attempt.patch_ref.sha256,
                    "diagnostics": diagnostics,
                    "unregistered_files": unregistered,
                    "containers": config.get("observed_containers", []),
                }
            ).encode()

        try:
            async with asyncio.timeout_at(deadline):
                await asyncio.to_thread(
                    _attempt_path(attempt, "evaluation-summary.json").write_bytes,
                    summary_bytes(collecting=True),
                )
                await asyncio.to_thread(
                    _attempt_path(attempt, "evaluation-config.json").write_bytes,
                    json.dumps(config).encode(),
                )
                for kind, name in paths:
                    try:
                        path = _attempt_path(attempt, name)
                        if not path.is_file():
                            if error is None and not cancelled.is_set():
                                raise FileNotFoundError(
                                    f"Necessary evidence missing: {name}"
                                )
                            continue
                        content = await asyncio.to_thread(path.read_bytes)
                        refs.append(await publish(kind, name, content))
                        del content
                    except (
                        ManagedExecutionError,
                        RunPersistenceError,
                        InterruptionRequested,
                    ):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        unregistered.append(name)
                        diagnostics.append(
                            f"Cannot register {name}: {type(exc).__name__}: {exc}"[
                                :4096
                            ]
                        )
                        if error is None and not cancelled.is_set():
                            error = EvaluationError(
                                code="EVALUATION_ARTIFACT_FAILED",
                                message=f"Necessary evidence could not be registered: {name}",
                            )
                content = summary_bytes()
                await asyncio.to_thread(
                    _attempt_path(attempt, "evaluation-summary.json").write_bytes,
                    content,
                )
                refs.append(
                    await publish(
                        "evaluation_summary", "evaluation-summary.json", content
                    )
                )
        except (ManagedExecutionError, RunPersistenceError, InterruptionRequested):
            raise
        except Exception as exc:  # noqa: BLE001
            diagnostics.append(
                f"Evidence collection incomplete at {attempt.directory}: {type(exc).__name__}: {exc}"[
                    :4096
                ]
            )
            if error is None and not cancelled.is_set():
                error = EvaluationError(
                    code="EVALUATION_ARTIFACT_FAILED", message=diagnostics[-1]
                )
        return refs, error

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        observer = self.observer_getter()
        if observer is not None and observer.progress.result is not None:
            return observer.progress.result
        if observer is not None and observer.progress.attempt is not None:
            envelope = observer.progress.attempt
            if envelope.kind != "swebench_attempt" or envelope.schema_version != 1:
                raise EvaluationAttemptBusy("Unsupported saved evaluation attempt")
            old = SWEbenchAttemptState.model_validate(envelope.payload)
            if (
                old.patch_ref != request.candidate.patch
                or old.task_digest != self.prepared.record_sha256
                or old.instance_id != self.prepared.record.instance_id
            ):
                raise EvaluationAttemptBusy(
                    "Saved evaluation attempt belongs to another patch or task"
                )
            await observer.record(
                EvaluationAttemptInterrupted(attempt_id=old.attempt_id),
                component=envelope,
            )
        try:
            attempt = await prepare_attempt(
                request,
                prepared=self.prepared,
                artifact_store=self.artifact_store,
                work_root=self.settings.swebench_work_root,
                model_name=self.model_name,
                timeout_seconds=self.settings.swebench_artifact_timeout_seconds,
                observer=observer,
            )
        except EvaluationInputError as exc:
            return EvaluationResult(
                passed=False,
                error=EvaluationError(code=exc.code, message=str(exc)[:4096]),
            )
        scope = (
            execution_scope(observer.execution_observer(attempt.attempt_id))
            if observer
            else nullcontext()
        )
        with scope:
            return await self._evaluate_attempt(request, attempt)

    async def _evaluate_attempt(
        self, request: EvaluationRequest, attempt: EvaluationAttempt
    ) -> EvaluationResult:
        process: asyncio.subprocess.Process | None = None
        identity: HostProcessIdentity | None = None
        cancelled: asyncio.CancelledError | None = None
        error: EvaluationError | None = None
        diagnostics: list[str] = []
        config = json.loads(
            await asyncio.to_thread(
                _attempt_path(attempt, "evaluation-config.json").read_bytes
            )
        )
        argv = [
            str(self.executable),
            "eval",
            str(_attempt_path(attempt, "task.json")),
            "--predictions",
            str(_attempt_path(attempt, "predictions.jsonl")),
            "--instance",
            attempt.instance_id,
            "--run-id",
            attempt.harness_run_id,
            "--workers",
            "1",
            "--timeout",
            str(self.settings.swebench_test_timeout_seconds),
            "--report-dir",
            str(_attempt_path(attempt, "reports")),
        ]
        config.update(
            {
                "harness_executable": str(self.executable),
                "argv": argv,
                "daemon_id": self.connection.daemon_id,
                "docker_connection": {
                    "source_context": self.connection.source_context,
                    "endpoint": self.connection.endpoint,
                    "tls_verify": self.connection.tls_verify,
                    "cert_path": str(self.connection.cert_path)
                    if self.connection.cert_path
                    else None,
                },
                "test_timeout_seconds": self.settings.swebench_test_timeout_seconds,
                "process_timeout_seconds": self.settings.swebench_process_timeout_seconds,
                "cleanup_timeout_seconds": self.settings.swebench_cleanup_timeout_seconds,
                "artifact_timeout_seconds": self.settings.swebench_artifact_timeout_seconds,
                "allow_image_pull": self.settings.swebench_allow_image_pull,
            }
        )
        stage = "precheck"
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.swebench_process_timeout_seconds
        )
        try:
            async with asyncio.timeout_at(deadline):
                await check_daemon(
                    self.docker_executable,
                    self.connection,
                    timeout_seconds=min(
                        10.0, max(0.001, deadline - asyncio.get_running_loop().time())
                    ),
                )
                image_id = await inspect_image(
                    self.docker_executable,
                    self.connection,
                    self.prepared.actual_image,
                    timeout_seconds=min(
                        10.0, max(0.001, deadline - asyncio.get_running_loop().time())
                    ),
                )
                config["image_precheck_id"] = image_id
                if image_id is None:
                    raise EvaluationPreparationError(
                        "EVALUATION_IMAGE_UNAVAILABLE", self.prepared.actual_image
                    )
                stage = "start"
                with (
                    _attempt_path(attempt, "stdout.log").open("wb") as stdout,
                    _attempt_path(attempt, "stderr.log").open("wb") as stderr,
                ):
                    observer = current_execution_observer()
                    if observer is not None:
                        observer.check_permission()
                    process, identity = await start_host_process(
                        argv,
                        role="harness",
                        attempt_id=observer.attempt_id
                        if observer
                        else attempt.attempt_id,
                        observer=observer,
                        deadline=deadline,
                        cwd=attempt.directory,
                        env=self.environment,
                        stdout=stdout,
                        stderr=stderr,
                    )
                    stage = "process"
                    await process.wait()
            async with asyncio.timeout(self.settings.swebench_artifact_timeout_seconds):
                assert process is not None and process.returncode is not None
                error = await self._judge(attempt, process.returncode)
        except (ManagedExecutionError, RunPersistenceError, InterruptionRequested):
            await _stop_harness(
                process, min(deadline, current_cleanup_deadline() or float("inf"))
            )
            raise
        except asyncio.CancelledError as exc:
            cancelled = exc
        except EvaluationPreparationError as exc:
            error = EvaluationError(code=exc.code, message=str(exc)[:4096])
        except TimeoutError:
            error = EvaluationError(
                code="EVALUATION_PROCESS_TIMEOUT",
                message="Independent evaluation exceeded its process budget",
            )
        except Exception as exc:  # noqa: BLE001
            error = EvaluationError(
                code="HARNESS_START_FAILED"
                if stage == "start"
                else "HARNESS_PROCESS_FAILED",
                message=f"{type(exc).__name__}: {exc}"[:4096],
            )
        cleaning = asyncio.create_task(self._cleanup(attempt, process, identity))
        try:
            identities, cleanup_diagnostics = await asyncio.shield(cleaning)
        except asyncio.CancelledError as exc:
            cancelled = cancelled or exc
            identities, cleanup_diagnostics = await asyncio.shield(cleaning)
        diagnostics.extend(cleanup_diagnostics)
        config["observed_containers"] = identities
        config["actual_image_id"] = identities[0]["image_id"] if identities else None
        config["image_id_source"] = "container_inspect" if identities else "unobserved"
        config["image_id_reason"] = (
            None
            if identities
            else "Container was not observed; precheck ID is not actual-container proof"
        )
        if cleanup_diagnostics and error is None and cancelled is None:
            error = EvaluationError(
                code="EVALUATION_CLEANUP_FAILED", message=cleanup_diagnostics[0][:4096]
            )
        cancellation_observed = asyncio.Event()
        if cancelled is not None:
            cancellation_observed.set()
        collecting = asyncio.create_task(
            self._collect(
                request, attempt, error, diagnostics, cancellation_observed, config
            )
        )
        try:
            refs, error = await asyncio.shield(collecting)
        except asyncio.CancelledError as exc:
            cancelled = cancelled or exc
            cancellation_observed.set()
            refs, error = await asyncio.shield(collecting)
        if cancelled is not None:
            cancelled.add_note(f"Evaluation attempt retained at {attempt.directory}")
            for diagnostic in diagnostics:
                cancelled.add_note(diagnostic)
            raise cancelled
        return EvaluationResult(
            passed=error is None,
            output=request.candidate.text if error is None else None,
            error=error,
            artifacts=refs,
        )
