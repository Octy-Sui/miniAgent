import asyncio
import hashlib
import math
import os
import re
import shutil
import signal
import stat
import tarfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from io import BytesIO
from pathlib import Path, PurePosixPath
from time import monotonic
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactStore, ArtifactWrite
from miniagent.environments.attachment import (
    AttachmentFacts,
    AttachmentProblem,
    LocalAttachment,
    WorkspaceAttachmentError,
)
from miniagent.environments.config import LocalWorkspaceSpec
from miniagent.environments.contracts import (
    Command,
    ExecutionResult,
    ExecutionStatus,
    Patch,
    PatchExportCleanupError,
    WorkspaceCheckpoint,
)
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    current_execution_observer,
)
from miniagent.environments.process import (
    decode_output,
    identify_host_process,
    inspect_host_execution,
    process_group_absent,
    read_stream,
    reap_process,
    register_host_process,
    write_stdin,
)


class LocalWorkspaceError(RuntimeError):
    pass


class LocalWorkspaceState(StrEnum):
    ACTIVE = "active"
    BROKEN = "broken"
    DESTROYED = "destroyed"


@dataclass(frozen=True, slots=True)
class LocalCommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class LocalWorkspaceBackend:
    def __init__(
        self,
        *,
        root: Path,
        artifact_store: ArtifactStore,
        max_output_bytes: int = 1024 * 1024,
    ) -> None:
        if max_output_bytes < 1:
            raise ValueError("Local command output limit must be positive")
        self.root = root.expanduser().resolve()
        self.artifact_store = artifact_store
        self.max_output_bytes = max_output_bytes

    async def create(self, spec: LocalWorkspaceSpec) -> "LocalWorkspace":
        self._validate_source(spec.options.source_dir)
        workspace_id = uuid4().hex
        workspace_dir = self._workspace_dir(spec.run_id, workspace_id)
        repository = workspace_dir / "repo"
        try:
            repository.mkdir(parents=True)
            paths = await self._source_paths(spec.options.source_dir)
            await asyncio.to_thread(
                self._copy_source_files,
                spec.options.source_dir,
                repository,
                paths,
            )
            await self._initialize_baseline(repository)
            base_revision = (
                (
                    await self._require_success(
                        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
                        cwd=repository,
                        action="read local workspace baseline",
                        capture_all=True,
                    )
                )
                .stdout.decode()
                .strip()
            )
            self._write_marker(workspace_dir, spec.run_id, workspace_id)
            return LocalWorkspace(
                backend=self,
                spec=spec,
                _workspace_id=workspace_id,
                root=repository,
                base_revision=base_revision,
            )
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, workspace_dir, True)
            raise

    @staticmethod
    def _require_original_path(path: Path) -> None:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Retained workspace path is not canonical")
        for component in reversed((path, *path.parents)):
            if stat.S_ISLNK(component.lstat().st_mode):
                raise ValueError("Retained workspace path is redirected by a symlink")

    async def inspect_attachment(self, attachment: LocalAttachment) -> AttachmentFacts:
        checked: list[str] = []
        errors: tuple[AttachmentProblem, ...] = ()
        exists = attachment.repository_root.exists()
        try:
            if (
                attachment.root != self.root
                or attachment.workspace_dir
                != self._workspace_dir(attachment.run_id, attachment.workspace_id)
                or attachment.repository_root != attachment.workspace_dir / "repo"
            ):
                raise ValueError(
                    "Retained workspace paths do not match original identity"
                )
            if Path(
                attachment.workspace_id
            ).name != attachment.workspace_id or attachment.workspace_id in (".", ".."):
                raise ValueError("Invalid retained workspace ID")
            if (
                attachment.marker_run_id != attachment.run_id
                or attachment.marker_workspace_id != attachment.workspace_id
            ):
                raise ValueError("Retained marker identity does not match workspace")
            for path in (
                attachment.root,
                attachment.workspace_dir,
                attachment.repository_root,
                attachment.repository_root / ".git",
            ):
                self._require_original_path(path)
                if not path.is_dir():
                    raise ValueError("Retained workspace directory is not a directory")
            checked.append("paths")
            marker = attachment.workspace_dir / ".miniagent-workspace"
            self._require_original_path(marker)
            if marker.read_text() != self._marker_content(
                attachment.run_id, attachment.workspace_id
            ):
                raise ValueError("Retained workspace marker identity differs")
            checked.append("marker")
            prefix = [
                "git",
                "--no-optional-locks",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
            ]
            top = await self._run(
                [*prefix, "rev-parse", "--show-toplevel"],
                cwd=attachment.repository_root,
                timeout_seconds=5.0,
            )
            if (
                top.exit_code
                or Path(top.stdout.decode().strip()) != attachment.repository_root
            ):
                raise ValueError("Retained repository root differs")
            checked.append("git_root")
            if (
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", attachment.base_revision)
                is None
            ):
                raise ValueError("Invalid retained Git baseline")
            baseline = await self._run(
                [*prefix, "cat-file", "-e", f"{attachment.base_revision}^{{commit}}"],
                cwd=attachment.repository_root,
                timeout_seconds=5.0,
            )
            if baseline.exit_code:
                raise ValueError("Original Git baseline is unavailable")
            checked.append("baseline")
        except FileNotFoundError:
            errors = (
                AttachmentProblem(
                    code="WORKSPACE_MISSING",
                    message="Retained workspace directory or marker is missing",
                ),
            )
        except ValueError as exc:
            errors = (
                AttachmentProblem(code="WORKSPACE_IDENTITY_MISMATCH", message=str(exc)),
            )
        except (OSError, TimeoutError, ManagedExecutionError):
            errors = (
                AttachmentProblem(
                    code="WORKSPACE_UNAVAILABLE",
                    message="Retained workspace metadata cannot be inspected",
                ),
            )
        return AttachmentFacts(
            run_id=attachment.run_id,
            workspace_id=attachment.workspace_id,
            exists=exists,
            identity_matches=not errors,
            checked=tuple(checked),
            errors=errors,
        )

    async def attach(
        self, spec: LocalWorkspaceSpec, attachment: LocalAttachment
    ) -> "LocalWorkspace":
        if spec.run_id != attachment.run_id:
            raise WorkspaceAttachmentError(
                AttachmentFacts(
                    run_id=attachment.run_id,
                    workspace_id=attachment.workspace_id,
                    exists=attachment.repository_root.exists(),
                    identity_matches=False,
                    errors=(
                        AttachmentProblem(
                            code="WORKSPACE_IDENTITY_MISMATCH",
                            message="Workspace and requested Run differ",
                        ),
                    ),
                )
            )
        facts = await self.inspect_attachment(attachment)
        if facts.errors or not facts.identity_matches:
            raise WorkspaceAttachmentError(facts)
        return LocalWorkspace(
            backend=self,
            spec=spec,
            _workspace_id=attachment.workspace_id,
            root=attachment.repository_root,
            base_revision=attachment.base_revision,
        )

    async def recover(
        self,
        spec: LocalWorkspaceSpec,
        checkpoint: WorkspaceCheckpoint,
    ) -> "LocalWorkspace":
        if checkpoint.run_id != spec.run_id:
            raise ValueError("Workspace checkpoint belongs to another run")
        if checkpoint.artifact is None or checkpoint.base_revision is None:
            raise ValueError("Local workspace checkpoint has no restorable content")
        if checkpoint.workdir != ".":
            raise ValueError("Workspace checkpoint is not a local workspace")

        workspace_id = uuid4().hex
        workspace_dir = self._workspace_dir(spec.run_id, workspace_id)
        try:
            content = await self.artifact_store.read(checkpoint.artifact)
            if hashlib.sha256(content).hexdigest() != checkpoint.revision:
                raise ValueError("Workspace checkpoint content does not match revision")
            workspace_dir.mkdir(parents=True)
            await asyncio.to_thread(self._extract_checkpoint, content, workspace_dir)
            repository = workspace_dir / "repo"
            base_revision = (
                (
                    await self._require_success(
                        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
                        cwd=repository,
                        action="read recovered local workspace baseline",
                        capture_all=True,
                    )
                )
                .stdout.decode()
                .strip()
            )
            if base_revision != checkpoint.base_revision:
                raise ValueError("Local workspace checkpoint baseline changed")
            self._write_marker(workspace_dir, spec.run_id, workspace_id)
            return LocalWorkspace(
                backend=self,
                spec=spec,
                _workspace_id=workspace_id,
                root=repository,
                base_revision=base_revision,
            )
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, workspace_dir, True)
            raise

    def _validate_source(self, source: Path) -> None:
        if not source.is_dir():
            raise ValueError(f"Local workspace source is not a directory: {source}")
        if self.root == source or self.root.is_relative_to(source):
            raise ValueError(
                "Local workspace root cannot be inside the source directory"
            )
        if source.is_relative_to(self.root):
            raise ValueError(
                "Local workspace source cannot be inside the workspace root"
            )

    async def _source_paths(self, source: Path) -> list[PurePosixPath]:
        top_level = (
            (
                await self._require_success(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=source,
                    action="inspect local workspace source",
                    capture_all=True,
                )
            )
            .stdout.decode()
            .strip()
        )
        if Path(top_level).resolve() != source:
            raise ValueError("Local workspace source must be the Git worktree root")
        result = await self._require_success(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=source,
            action="list local workspace source files",
            capture_all=True,
        )
        return [
            PurePosixPath(path.decode("utf-8"))
            for path in result.stdout.split(b"\0")
            if path
        ]

    @staticmethod
    def _copy_source_files(
        source: Path,
        target: Path,
        paths: list[PurePosixPath],
    ) -> None:
        for relative in paths:
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Git returned an unsafe source path: {relative}")
            source_path = source.joinpath(*relative.parts)
            if not source_path.exists() and not source_path.is_symlink():
                continue
            target_path = target.joinpath(*relative.parts)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if source_path.is_symlink():
                link_target = Path(os.readlink(source_path))
                if (
                    link_target.is_absolute()
                    or not source_path.resolve().is_relative_to(source)
                ):
                    raise ValueError(f"Source symlink escapes the worktree: {relative}")
                target_path.symlink_to(link_target)
            elif source_path.is_file():
                shutil.copy2(source_path, target_path)
            elif source_path.is_dir():
                raise ValueError(f"Git submodules are not supported: {relative}")
            else:
                raise ValueError(f"Unsupported source file type: {relative}")

    async def _initialize_baseline(self, repository: Path) -> None:
        await self._require_success(
            ["git", "init", "--quiet"],
            cwd=repository,
            action="initialize local workspace",
        )
        await self._require_success(
            ["git", "add", "-A", "--", "."],
            cwd=repository,
            action="stage local workspace baseline",
        )
        await self._require_success(
            [
                "git",
                "-c",
                "user.name=MiniAgent",
                "-c",
                "user.email=miniagent@localhost",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "miniagent workspace baseline",
            ],
            cwd=repository,
            action="commit local workspace baseline",
        )

    async def _require_success(
        self,
        argv: list[str],
        *,
        cwd: Path,
        action: str,
        environment: dict[str, str] | None = None,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> LocalCommandResult:
        result = await self._run(
            argv,
            cwd=cwd,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            capture_all=capture_all,
            cleanup_deadline=cleanup_deadline,
        )
        if result.exit_code != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            detail = stderr or f"command exited with code {result.exit_code}"
            raise LocalWorkspaceError(f"Failed to {action}: {detail}")
        return result

    async def _run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> LocalCommandResult:
        started_at = monotonic()
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=self._environment(environment),
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise LocalWorkspaceError("Local process output pipes are unavailable")

        limit = None
        if not capture_all:
            limit = (
                self.max_output_bytes if max_output_bytes is None else max_output_bytes
            )
        stdout_task = asyncio.create_task(read_stream(process.stdout, limit))
        stderr_task = asyncio.create_task(read_stream(process.stderr, limit))
        wait_task = asyncio.create_task(process.wait())
        tasks: list[asyncio.Task[object]] = [stdout_task, stderr_task, wait_task]
        if stdin is not None:
            tasks.append(
                asyncio.create_task(
                    write_stdin(
                        process,
                        stdin,
                        unavailable_error=LocalWorkspaceError(
                            "Local process input pipe is unavailable"
                        ),
                    )
                )
            )

        if cleanup_deadline is not None:
            remaining = max(0.0, cleanup_deadline - monotonic())
            timeout_seconds = min(
                timeout_seconds if timeout_seconds is not None else remaining,
                max(0.0, remaining - min(0.1, remaining / 4)),
            )

        observer = current_execution_observer()
        identity = None
        try:
            try:
                identity = identify_host_process(
                    process.pid,
                    attempt_id=observer.attempt_id if observer is not None else uuid4(),
                    execution_id=uuid4(),
                    role="local_command",
                )
            except ManagedExecutionError:
                if observer is not None or not process_group_absent(process.pid):
                    raise
            if identity is not None:
                register_host_process(process, identity)
                if observer is not None:
                    await observer.identified(identity)
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout_seconds)
            if identity is not None:
                evidence = inspect_host_execution(identity)
                if evidence.outcome != "ended":
                    raise ManagedExecutionError(
                        "Local command whole group end is unconfirmed"
                    )
                if observer is not None:
                    await observer.ended(evidence)
            elif not process_group_absent(process.pid):
                raise ManagedExecutionError("Local command group end is unconfirmed")
        except BaseException as exc:
            diagnostics = await reap_process(
                process, tasks, deadline=cleanup_deadline or monotonic() + 5.0
            )
            for diagnostic in diagnostics:
                exc.add_note(diagnostic)
            if diagnostics:
                raise ManagedExecutionError(
                    "Local command cleanup is unconfirmed"
                ) from exc
            if (
                isinstance(exc, (TimeoutError, asyncio.CancelledError))
                and identity is not None
                and observer is not None
            ):
                await observer.ended(inspect_host_execution(identity))
            raise

        if process.returncode is None:
            raise LocalWorkspaceError("Local process ended without an exit code")
        stdout, stdout_truncated = stdout_task.result()
        stderr, stderr_truncated = stderr_task.result()
        return LocalCommandResult(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((monotonic() - started_at) * 1000),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    @staticmethod
    def _environment(overrides: dict[str, str] | None) -> dict[str, str]:
        environment = {
            name: value
            for name, value in os.environ.items()
            if name == "PATH" or name == "LANG" or name.startswith("LC_")
        }
        environment.update(overrides or {})
        return environment

    @staticmethod
    async def _kill_process_group(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    def _workspace_dir(self, run_id: UUID, workspace_id: str) -> Path:
        return self.root / run_id.hex / workspace_id

    @staticmethod
    def _marker_content(run_id: UUID, workspace_id: str) -> str:
        return f"{run_id}\n{workspace_id}\n"

    def _write_marker(
        self,
        workspace_dir: Path,
        run_id: UUID,
        workspace_id: str,
    ) -> None:
        (workspace_dir / ".miniagent-workspace").write_text(
            self._marker_content(run_id, workspace_id),
            encoding="utf-8",
        )

    @staticmethod
    def _checkpoint_content(repository: Path) -> bytes:
        content = BytesIO()
        with tarfile.open(fileobj=content, mode="w") as archive:
            archive.add(repository, arcname="repo", recursive=True)
        return content.getvalue()

    @staticmethod
    def _extract_checkpoint(content: bytes, workspace_dir: Path) -> None:
        with tarfile.open(fileobj=BytesIO(content), mode="r:") as archive:
            archive.extractall(workspace_dir, filter="data")


@dataclass(slots=True)
class LocalWorkspace:
    backend: LocalWorkspaceBackend
    spec: LocalWorkspaceSpec
    _workspace_id: str
    root: Path
    base_revision: str
    state: LocalWorkspaceState = LocalWorkspaceState.ACTIVE
    _operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def run_id(self) -> UUID:
        return self.spec.run_id

    def describe_attachment(self) -> LocalAttachment:
        return LocalAttachment(
            run_id=self.run_id,
            workspace_id=self.workspace_id,
            base_revision=self.base_revision,
            root=self.backend.root,
            workspace_dir=self.root.parent,
            repository_root=self.root,
            marker_run_id=self.run_id,
            marker_workspace_id=self.workspace_id,
        )

    async def execute(self, command: Command) -> ExecutionResult:
        async with self._operation_lock:
            self._require_active()
            started_at = monotonic()
            try:
                result = await self.backend._run(
                    command.argv,
                    cwd=self._resolve_cwd(command.cwd),
                    environment=self.spec.environment_values | command.environment,
                    stdin=command.stdin.encode() if command.stdin is not None else None,
                    timeout_seconds=command.timeout_seconds,
                )
            except TimeoutError:
                self.state = LocalWorkspaceState.BROKEN
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    duration_ms=int((monotonic() - started_at) * 1000),
                )
            except asyncio.CancelledError:
                self.state = LocalWorkspaceState.BROKEN
                raise

            return ExecutionResult(
                status=(
                    ExecutionStatus.SUCCESS
                    if result.exit_code == 0
                    else ExecutionStatus.ERROR
                ),
                exit_code=result.exit_code,
                stdout=decode_output(result.stdout, result.stdout_truncated),
                stderr=decode_output(result.stderr, result.stderr_truncated),
                stdout_truncated=result.stdout_truncated,
                stderr_truncated=result.stderr_truncated,
                duration_ms=result.duration_ms,
            )

    async def checkpoint(self) -> WorkspaceCheckpoint:
        async with self._operation_lock:
            self._require_active()
            content = await asyncio.to_thread(
                self.backend._checkpoint_content,
                self.root,
            )
            revision = hashlib.sha256(content).hexdigest()
            checkpoint_id = uuid4().hex
            artifact = await self.backend.artifact_store.put(
                ArtifactWrite(
                    run_id=self.run_id,
                    type="workspace_checkpoint",
                    name=f"{checkpoint_id}.tar",
                    media_type="application/x-tar",
                    metadata={
                        "backend": "local",
                        "checkpoint_id": checkpoint_id,
                        "workspace_id": self.workspace_id,
                        "revision": revision,
                        "base_revision": self.base_revision,
                        "workdir": ".",
                    },
                ),
                content,
            )
            return WorkspaceCheckpoint(
                checkpoint_id=checkpoint_id,
                run_id=self.run_id,
                workspace_id=self.workspace_id,
                revision=revision,
                created_at=datetime.now(UTC),
                base_revision=self.base_revision,
                workdir=".",
                artifact=artifact,
            )

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None:
        async with self._operation_lock:
            self._require_not_destroyed()
            if checkpoint.run_id != self.run_id:
                raise ValueError("Workspace checkpoint belongs to another run")
            if checkpoint.artifact is None:
                raise ValueError("Workspace checkpoint has no artifact")
            if checkpoint.base_revision != self.base_revision:
                raise ValueError("Workspace checkpoint uses another Git baseline")
            if checkpoint.workdir != ".":
                raise ValueError("Workspace checkpoint is not a local workspace")
            content = await self.backend.artifact_store.read(checkpoint.artifact)
            if hashlib.sha256(content).hexdigest() != checkpoint.revision:
                raise ValueError("Workspace checkpoint content does not match revision")

            repository = self.root
            replacement = repository.with_name(f"repo-restore-{uuid4().hex}")
            replacement.mkdir()
            try:
                await asyncio.to_thread(
                    self.backend._extract_checkpoint,
                    content,
                    replacement,
                )
                restored = replacement / "repo"
                restored_revision = (
                    (
                        await self.backend._require_success(
                            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
                            cwd=restored,
                            action="validate restored local workspace",
                            capture_all=True,
                        )
                    )
                    .stdout.decode()
                    .strip()
                )
                if restored_revision != self.base_revision:
                    raise ValueError("Workspace checkpoint Git baseline changed")
                await asyncio.to_thread(shutil.rmtree, repository)
                restored.rename(repository)
            except BaseException:
                self.state = LocalWorkspaceState.BROKEN
                raise
            finally:
                await asyncio.to_thread(shutil.rmtree, replacement, True)
            self.state = LocalWorkspaceState.ACTIVE

    async def export_patch(
        self,
        *,
        timeout_seconds: float = 60.0,
        cleanup_timeout_seconds: float = 5.0,
        temporary_index_path: str | None = None,
    ) -> Patch:
        if not (
            math.isfinite(timeout_seconds)
            and math.isfinite(cleanup_timeout_seconds)
            and 0 < cleanup_timeout_seconds < timeout_seconds
        ):
            raise ValueError(
                "Export cleanup timeout must be positive and below total timeout"
            )
        deadline = monotonic() + timeout_seconds
        work_deadline = deadline - cleanup_timeout_seconds
        acquired = False
        temporary_index = None
        primary: BaseException | None = None
        try:
            async with asyncio.timeout_at(work_deadline):
                await self._operation_lock.acquire()
                acquired = True
                self._require_active()
                candidate_index = (
                    Path(temporary_index_path)
                    if temporary_index_path is not None
                    else self.root.parent / f"index-{uuid4().hex}"
                )
                if (
                    not candidate_index.is_absolute()
                    or ".." in candidate_index.parts
                    or candidate_index.parent != self.root.parent
                    or re.fullmatch(r"index-[0-9a-f]{32}", candidate_index.name) is None
                    or candidate_index.exists()
                    or candidate_index.is_symlink()
                ):
                    raise ValueError(
                        "Temporary index must be a new owned workspace index"
                    )
                self.backend._require_original_path(candidate_index.parent)
                temporary_index = candidate_index
                environment = self.spec.environment_values | {
                    "GIT_INDEX_FILE": str(temporary_index)
                }
                await self.backend._require_success(
                    ["git", "read-tree", self.base_revision],
                    cwd=self.root,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="prepare local workspace patch",
                )
                await self.backend._require_success(
                    ["git", "add", "-A", "--", "."],
                    cwd=self.root,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="index local workspace changes",
                )
                patch = await self.backend._require_success(
                    [
                        "git",
                        "diff",
                        "--cached",
                        "--binary",
                        "--full-index",
                        self.base_revision,
                        "--",
                        ".",
                    ],
                    cwd=self.root,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="export local workspace patch",
                    capture_all=True,
                )
                changed_files = await self.backend._require_success(
                    [
                        "git",
                        "diff",
                        "--cached",
                        "--name-only",
                        "-z",
                        self.base_revision,
                        "--",
                        ".",
                    ],
                    cwd=self.root,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="list local workspace changes",
                    capture_all=True,
                )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                if temporary_index is not None:
                    cleanup_deadline = min(
                        deadline, monotonic() + cleanup_timeout_seconds
                    )

                    async def cleanup_index() -> None:
                        async with asyncio.timeout_at(cleanup_deadline):
                            await asyncio.to_thread(
                                temporary_index.unlink, missing_ok=True
                            )

                    cleaning = asyncio.create_task(cleanup_index())
                    try:
                        await asyncio.shield(cleaning)
                    except asyncio.CancelledError as exc:
                        try:
                            await asyncio.shield(cleaning)
                        except Exception as cleanup_error:  # noqa: BLE001
                            exc.add_note(
                                f"Patch export cleanup failed: temporary index {temporary_index}: {cleanup_error}"
                            )
                        raise
                    except Exception as cleanup_error:
                        detail = f"Patch export cleanup failed: temporary index {temporary_index}: {cleanup_error}"
                        if primary is not None:
                            primary.add_note(detail)
                        else:
                            raise PatchExportCleanupError(detail) from cleanup_error
            finally:
                if acquired:
                    self._operation_lock.release()

        return Patch(
            content=patch.stdout.decode("utf-8", errors="replace"),
            files=[
                path.decode("utf-8", errors="replace")
                for path in changed_files.stdout.split(b"\0")
                if path
            ],
            base_revision=self.base_revision,
        )

    async def destroy(self) -> None:
        async with self._operation_lock:
            if self.state is LocalWorkspaceState.DESTROYED:
                return
            workspace_dir = self.root.parent.resolve()
            expected = self.backend._workspace_dir(
                self.run_id,
                self.workspace_id,
            ).resolve()
            if workspace_dir != expected or not workspace_dir.is_relative_to(
                self.backend.root
            ):
                raise LocalWorkspaceError(
                    "Refusing to destroy an unsafe workspace path"
                )
            marker = workspace_dir / ".miniagent-workspace"
            if marker.read_text(encoding="utf-8") != self.backend._marker_content(
                self.run_id,
                self.workspace_id,
            ):
                raise LocalWorkspaceError("Local workspace identity marker changed")
            await asyncio.to_thread(shutil.rmtree, workspace_dir)
            with suppress(OSError):
                workspace_dir.parent.rmdir()
            self.state = LocalWorkspaceState.DESTROYED

    def _resolve_cwd(self, cwd: str) -> Path:
        requested = Path(cwd)
        candidate = requested if requested.is_absolute() else self.root / requested
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Command working directory escapes the workspace")
        return resolved

    def _require_active(self) -> None:
        if self.state is not LocalWorkspaceState.ACTIVE:
            raise LocalWorkspaceError(
                f"Local workspace is not active: {self.state.value}"
            )

    def _require_not_destroyed(self) -> None:
        if self.state is LocalWorkspaceState.DESTROYED:
            raise LocalWorkspaceError("Local workspace has been destroyed")
