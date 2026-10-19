# File   : docker.py
# Created: 2026-09-15 18:39:26
# Author : octy Sui

import asyncio
import hashlib
import math
import os
import posixpath
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from time import monotonic
from uuid import UUID, uuid4

from miniagent.artifacts import ArtifactStore, ArtifactWrite
from miniagent.environments.config import ContainerUserPolicy, DockerWorkspaceSpec
from miniagent.environments.contracts import (
    Command,
    ExecutionResult,
    ExecutionStatus,
    Patch,
    PatchExportCleanupError,
    WorkspaceCheckpoint,
)
from miniagent.environments.docker_connection import (
    DockerConnection,
    docker_argv,
    harness_environment,
    resolve_docker_connection,
)
from miniagent.environments.docker_exec import run_docker_exec_output
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    MemoryExecutionObserver,
    current_execution_observer,
)
from miniagent.environments.process import (
    decode_output,
    identify_host_process,
    inspect_host_execution,
    read_stream,
    reap_process,
    register_host_process,
    write_stdin,
)


class DockerWorkspaceError(RuntimeError):
    pass


class DockerCommandError(DockerWorkspaceError):
    pass


class DockerWorkspaceState(StrEnum):
    ACTIVE = "active"
    BROKEN = "broken"
    DESTROYED = "destroyed"


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class DockerWorkspaceBackend:
    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        docker_executable: str = "docker",
        max_output_bytes: int = 1024 * 1024,
        connection: DockerConnection | None = None,
    ) -> None:
        if max_output_bytes < 1:
            raise ValueError("Docker command output limit must be positive")
        self.artifact_store = artifact_store
        self.docker_executable = docker_executable
        self.max_output_bytes = max_output_bytes
        self.connection = connection

    async def create(self, spec: DockerWorkspaceSpec) -> "DockerWorkspace":
        image_id = await self._resolve_image_id(spec.options.image)
        return await self._create_from_image(spec, image_id)

    async def _create_from_image(
        self,
        spec: DockerWorkspaceSpec,
        image_id: str,
    ) -> "DockerWorkspace":
        workspace_id = uuid4().hex
        container_id: str | None = None

        try:
            container_id = await self._create_container(
                spec,
                workspace_id=workspace_id,
                image_id=image_id,
            )
            await self._require_success(
                ["container", "start", container_id],
                action="start Docker workspace",
            )
            await self._validate_workdir(container_id, spec)
            base_revision = await self._read_base_revision(container_id, spec)
            return DockerWorkspace(
                backend=self,
                spec=spec,
                _workspace_id=workspace_id,
                container_id=container_id,
                image_id=image_id,
                base_revision=base_revision,
            )
        except BaseException as exc:
            if container_id is not None:
                try:
                    await self._remove_container(container_id)
                except DockerWorkspaceError as cleanup_error:
                    exc.add_note(f"Workspace cleanup also failed: {cleanup_error}")
            raise

    async def recover(
        self,
        spec: DockerWorkspaceSpec,
        checkpoint: WorkspaceCheckpoint,
    ) -> "DockerWorkspace":
        if checkpoint.run_id != spec.run_id:
            raise ValueError("Workspace checkpoint belongs to another run")
        if (
            checkpoint.image_id is None
            or checkpoint.base_revision is None
            or checkpoint.workdir is None
        ):
            raise ValueError("Docker workspace checkpoint has no image baseline")
        if checkpoint.workdir != spec.options.workdir:
            raise ValueError("Docker workspace checkpoint uses another workdir")

        image_id = await self._resolve_image_id(checkpoint.image_id)
        workspace = await self._create_from_image(spec, image_id)
        try:
            if workspace.base_revision != checkpoint.base_revision:
                raise ValueError("Docker workspace checkpoint base revision changed")
            await workspace.restore(checkpoint)
        except BaseException as exc:
            try:
                await workspace.destroy()
            except DockerWorkspaceError as cleanup_error:
                exc.add_note(f"Workspace cleanup also failed: {cleanup_error}")
            raise
        return workspace

    async def _resolve_image_id(self, image: str) -> str:
        result = await self._require_success(
            ["image", "inspect", "--format", "{{.Id}}", image],
            action=f"inspect Docker image {image!r}",
        )
        image_id = result.stdout.decode().strip()
        if not image_id:
            raise DockerWorkspaceError(f"Docker image {image!r} has no image ID")
        return image_id

    async def _create_container(
        self,
        spec: DockerWorkspaceSpec,
        *,
        workspace_id: str,
        image_id: str,
    ) -> str:
        name = f"miniagent-{spec.run_id.hex}-{workspace_id[:12]}"
        argv = [
            "container",
            "create",
            "--name",
            name,
            "--hostname",
            f"miniagent-{spec.run_id.hex[:12]}",
            "--label",
            "miniagent.managed=true",
            "--label",
            f"miniagent.run_id={spec.run_id}",
            "--label",
            f"miniagent.workspace_id={workspace_id}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(spec.options.limits.pids),
            "--cpus",
            str(spec.options.limits.cpu_count),
            "--memory",
            f"{spec.options.limits.memory_mb}m",
            "--memory-swap",
            f"{spec.options.limits.memory_mb}m",
            "--network",
            "bridge" if spec.options.network_enabled else "none",
            "--init",
            "--workdir",
            spec.options.workdir,
        ]
        for name, value in sorted(spec.environment_values.items()):
            argv.extend(("--env", f"{name}={value}"))
        argv.extend(
            (
                "--entrypoint",
                "/bin/sh",
                image_id,
                "-c",
                "while :; do sleep 3600; done",
            )
        )
        result = await self._require_success(
            argv,
            action="create Docker workspace",
        )
        container_id = result.stdout.decode().strip()
        if not container_id:
            raise DockerWorkspaceError("Docker did not return a container ID")
        return container_id

    async def _validate_workdir(
        self,
        container_id: str,
        spec: DockerWorkspaceSpec,
    ) -> None:
        await self._require_exec_success(
            container_id,
            ["/bin/sh", "-c", "test -d . && test -w ."],
            cwd=spec.options.workdir,
            action=f"validate writable workspace {spec.options.workdir!r}",
        )
        if spec.options.user_policy is ContainerUserPolicy.REQUIRE_NON_ROOT:
            result = await self._require_exec_success(
                container_id,
                ["id", "-u"],
                cwd=spec.options.workdir,
                action="inspect Docker workspace user",
            )
            if result.stdout.decode().strip() == "0":
                raise DockerWorkspaceError(
                    "Docker workspace image runs as root but non-root is required"
                )

    async def _read_base_revision(
        self,
        container_id: str,
        spec: DockerWorkspaceSpec,
    ) -> str:
        workdir = spec.options.workdir
        git = ["git", "-c", f"safe.directory={workdir}", "-C", workdir]
        result = await self._require_exec_success(
            container_id,
            [*git, "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=spec.options.workdir,
            action="read workspace base revision",
        )
        status = await self._require_exec_success(
            container_id,
            [*git, "status", "--porcelain", "--untracked-files=all"],
            cwd=spec.options.workdir,
            action="verify workspace base is clean",
        )
        if status.stdout:
            raise DockerWorkspaceError(
                "Docker workspace image must contain a clean Git worktree"
            )
        return result.stdout.decode().strip()

    async def _exec(
        self,
        container_id: str,
        argv: list[str],
        *,
        cwd: str,
        environment: dict[str, str] | None = None,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> DockerCommandResult:
        command = ["container", "exec"]
        if stdin is not None:
            command.append("--interactive")
        command.extend(("--workdir", cwd))
        for name, value in sorted((environment or {}).items()):
            command.extend(("--env", f"{name}={value}"))
        command.extend((container_id, *argv))
        return await self._run(
            command,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            capture_all=capture_all,
            cleanup_deadline=cleanup_deadline,
        )

    async def _require_exec_success(
        self,
        container_id: str,
        argv: list[str],
        *,
        cwd: str,
        action: str,
        environment: dict[str, str] | None = None,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> DockerCommandResult:
        result = await self._exec(
            container_id,
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
            raise self._command_error(action, result)
        return result

    async def _require_success(
        self,
        argv: list[str],
        *,
        action: str,
    ) -> DockerCommandResult:
        result = await self._run(argv)
        if result.exit_code != 0:
            raise self._command_error(action, result)
        return result

    async def _kill_container(self, container_id: str) -> None:
        result = await self._run(["container", "kill", container_id])
        if result.exit_code != 0 and b"is not running" not in result.stderr:
            raise self._command_error("kill Docker workspace", result)

    async def _start_container(self, container_id: str) -> None:
        await self._require_success(
            ["container", "start", container_id],
            action="restart Docker workspace",
        )

    async def _remove_container(self, container_id: str) -> None:
        result = await self._run(["container", "rm", "--force", container_id])
        if result.exit_code != 0 and b"No such container" not in result.stderr:
            raise self._command_error("remove Docker workspace", result)

    async def _run(
        self,
        argv: list[str],
        *,
        stdin: bytes | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
        capture_all: bool = False,
        cleanup_deadline: float | None = None,
    ) -> DockerCommandResult:
        started_at = monotonic()
        observer = current_execution_observer() or MemoryExecutionObserver()
        if argv[:2] == ["container", "exec"]:
            if self.connection is None:
                self.connection = await resolve_docker_connection(
                    Path(self.docker_executable), dict(os.environ)
                )
            index = 2
            cwd, environment_values = "/", {}
            while index < len(argv) and argv[index].startswith("--"):
                option = argv[index]
                index += 1
                if option == "--interactive":
                    continue
                value = argv[index]
                index += 1
                if option == "--workdir":
                    cwd = value
                elif option == "--env":
                    name, value = value.split("=", 1)
                    environment_values[name] = value
                else:
                    raise ValueError(f"Unsupported managed exec option: {option}")
            budget = timeout_seconds if timeout_seconds is not None else 60.0
            deadline = min(started_at + budget, cleanup_deadline or math.inf)
            output = await run_docker_exec_output(
                Command(
                    argv=argv[index + 1 :],
                    cwd=cwd,
                    environment=environment_values,
                    timeout_seconds=budget,
                ),
                connection=self.connection,
                container_id=argv[index],
                observer=observer,
                attempt_id=observer.attempt_id,
                deadline=deadline,
                stdin_bytes=stdin,
                max_output_bytes=None
                if capture_all
                else (
                    self.max_output_bytes
                    if max_output_bytes is None
                    else max_output_bytes
                ),
            )
            if output.result.status is ExecutionStatus.TIMEOUT:
                raise TimeoutError(
                    "Docker command timed out after verified group cleanup"
                )
            assert output.result.exit_code is not None
            return DockerCommandResult(
                exit_code=output.result.exit_code,
                stdout=output.stdout,
                stderr=output.stderr,
                duration_ms=output.result.duration_ms,
                stdout_truncated=output.result.stdout_truncated,
                stderr_truncated=output.result.stderr_truncated,
            )
        command = (
            docker_argv(Path(self.docker_executable), self.connection, argv)
            if self.connection is not None
            else [self.docker_executable, *argv]
        )
        environment = (
            harness_environment(
                dict(self.connection.client_environment), self.connection
            )
            if self.connection is not None
            else None
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            env=environment,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        identity = identify_host_process(
            process.pid,
            attempt_id=observer.attempt_id,
            execution_id=uuid4(),
            role="docker_helper",
        )
        register_host_process(process, identity)
        try:
            await observer.identified(identity)
        except BaseException:
            await reap_process(
                process, [], deadline=cleanup_deadline or monotonic() + 5.0
            )
            raise
        if process.stdout is None or process.stderr is None:
            raise DockerWorkspaceError("Docker process output pipes are unavailable")

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
                        unavailable_error=DockerWorkspaceError(
                            "Docker process input pipe is unavailable"
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

        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=timeout_seconds,
            )
        except BaseException as exc:
            for diagnostic in await reap_process(
                process, tasks, deadline=cleanup_deadline or monotonic() + 5.0
            ):
                exc.add_note(diagnostic)
            raise

        if process.returncode is None:
            raise DockerWorkspaceError("Docker process ended without an exit code")
        evidence = inspect_host_execution(identity)
        if evidence.outcome != "ended":
            await reap_process(
                process, tasks, deadline=cleanup_deadline or monotonic() + 5.0
            )
            raise ManagedExecutionError("Docker CLI whole group end is unconfirmed")
        await observer.ended(evidence)
        stdout, stdout_truncated = stdout_task.result()
        stderr, stderr_truncated = stderr_task.result()
        return DockerCommandResult(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((monotonic() - started_at) * 1000),
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    @staticmethod
    def _command_error(
        action: str,
        result: DockerCommandResult,
    ) -> DockerCommandError:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = stderr or f"Docker exited with code {result.exit_code}"
        return DockerCommandError(f"Failed to {action}: {detail}")


@dataclass(slots=True)
class DockerWorkspace:
    backend: DockerWorkspaceBackend
    spec: DockerWorkspaceSpec
    _workspace_id: str
    container_id: str
    image_id: str
    base_revision: str
    state: DockerWorkspaceState = DockerWorkspaceState.ACTIVE
    _operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def workspace_id(self) -> str:
        return self._workspace_id

    @property
    def run_id(self) -> UUID:
        return self.spec.run_id

    async def execute(self, command: Command) -> ExecutionResult:
        async with self._operation_lock:
            self._require_active()
            started_at = monotonic()
            try:
                result = await self.backend._exec(
                    self.container_id,
                    command.argv,
                    cwd=self._resolve_cwd(command.cwd),
                    environment=command.environment,
                    stdin=command.stdin.encode() if command.stdin is not None else None,
                    timeout_seconds=command.timeout_seconds,
                )
            except TimeoutError:
                self.state = DockerWorkspaceState.BROKEN
                await self.backend._kill_container(self.container_id)
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    duration_ms=int((monotonic() - started_at) * 1000),
                )
            except asyncio.CancelledError:
                self.state = DockerWorkspaceState.BROKEN
                await asyncio.shield(self.backend._kill_container(self.container_id))
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
            result = await self.backend._require_exec_success(
                self.container_id,
                ["tar", "-C", self.spec.options.workdir, "-cf", "-", "."],
                cwd=self.spec.options.workdir,
                action="create workspace checkpoint",
                capture_all=True,
            )
            revision = hashlib.sha256(result.stdout).hexdigest()
            checkpoint_id = uuid4().hex
            artifact = await self.backend.artifact_store.put(
                ArtifactWrite(
                    run_id=self.run_id,
                    type="workspace_checkpoint",
                    name=f"{checkpoint_id}.tar",
                    media_type="application/x-tar",
                    metadata={
                        "backend": "docker",
                        "checkpoint_id": checkpoint_id,
                        "workspace_id": self.workspace_id,
                        "revision": revision,
                        "image_id": self.image_id,
                        "base_revision": self.base_revision,
                        "workdir": self.spec.options.workdir,
                    },
                ),
                result.stdout,
            )
            return WorkspaceCheckpoint(
                checkpoint_id=checkpoint_id,
                run_id=self.run_id,
                workspace_id=self.workspace_id,
                revision=revision,
                created_at=datetime.now(UTC),
                image_id=self.image_id,
                base_revision=self.base_revision,
                workdir=self.spec.options.workdir,
                artifact=artifact,
            )

    async def restore(self, checkpoint: WorkspaceCheckpoint) -> None:
        async with self._operation_lock:
            self._require_not_destroyed()
            if checkpoint.run_id != self.run_id:
                raise ValueError("Workspace checkpoint belongs to another run")
            if checkpoint.artifact is None:
                raise ValueError("Workspace checkpoint has no artifact")
            if checkpoint.image_id != self.image_id:
                raise ValueError("Workspace checkpoint uses another Docker image")
            if checkpoint.base_revision != self.base_revision:
                raise ValueError("Workspace checkpoint uses another Git baseline")
            if checkpoint.workdir != self.spec.options.workdir:
                raise ValueError("Workspace checkpoint uses another workdir")

            content = await self.backend.artifact_store.read(checkpoint.artifact)
            if hashlib.sha256(content).hexdigest() != checkpoint.revision:
                raise ValueError("Workspace checkpoint content does not match revision")

            if self.state is DockerWorkspaceState.BROKEN:
                await self.backend._start_container(self.container_id)
            try:
                await self.backend._require_exec_success(
                    self.container_id,
                    [
                        "find",
                        self.spec.options.workdir,
                        "-mindepth",
                        "1",
                        "-maxdepth",
                        "1",
                        "-exec",
                        "rm",
                        "-rf",
                        "--",
                        "{}",
                        "+",
                    ],
                    cwd=self.spec.options.workdir,
                    action="clear Docker workspace",
                )
                await self.backend._require_exec_success(
                    self.container_id,
                    ["tar", "-C", self.spec.options.workdir, "-xf", "-"],
                    cwd=self.spec.options.workdir,
                    action="restore workspace checkpoint",
                    stdin=content,
                )
            except BaseException:
                self.state = DockerWorkspaceState.BROKEN
                raise
            self.state = DockerWorkspaceState.ACTIVE

    async def export_patch(
        self, *, timeout_seconds: float = 60.0, cleanup_timeout_seconds: float = 5.0
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
                temporary_index = f"/tmp/miniagent-index-{uuid4().hex}"
                environment = {"GIT_INDEX_FILE": temporary_index}
                git = [
                    "git",
                    "-c",
                    f"safe.directory={self.spec.options.workdir}",
                    "-C",
                    self.spec.options.workdir,
                ]
                await self.backend._require_exec_success(
                    self.container_id,
                    [*git, "read-tree", self.base_revision],
                    cwd=self.spec.options.workdir,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="prepare workspace patch",
                )
                await self.backend._require_exec_success(
                    self.container_id,
                    [*git, "add", "-A", "--", "."],
                    cwd=self.spec.options.workdir,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="index workspace changes",
                )
                patch = await self.backend._require_exec_success(
                    self.container_id,
                    [
                        *git,
                        "diff",
                        "--cached",
                        "--binary",
                        "--full-index",
                        self.base_revision,
                        "--",
                        ".",
                    ],
                    cwd=self.spec.options.workdir,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="export workspace patch",
                    capture_all=True,
                )
                changed_files = await self.backend._require_exec_success(
                    self.container_id,
                    [
                        *git,
                        "diff",
                        "--cached",
                        "--name-only",
                        "-z",
                        self.base_revision,
                        "--",
                        ".",
                    ],
                    cwd=self.spec.options.workdir,
                    environment=environment,
                    timeout_seconds=max(0.0, work_deadline - monotonic()),
                    cleanup_deadline=deadline,
                    action="list workspace changes",
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
                            result = await self.backend._exec(
                                self.container_id,
                                ["rm", "-f", "--", temporary_index],
                                cwd=self.spec.options.workdir,
                                timeout_seconds=max(
                                    0.0, cleanup_deadline - monotonic()
                                ),
                                cleanup_deadline=cleanup_deadline,
                            )
                            if result.exit_code:
                                raise PatchExportCleanupError(
                                    "Docker index cleanup command failed"
                                )

                    cleaning = asyncio.create_task(cleanup_index())
                    try:
                        await asyncio.shield(cleaning)
                    except asyncio.CancelledError as exc:
                        try:
                            await asyncio.shield(cleaning)
                        except Exception as cleanup_error:  # noqa: BLE001
                            exc.add_note(
                                f"Patch export cleanup failed: temporary index {temporary_index} in container {self.container_id}: {cleanup_error}"
                            )
                        raise
                    except Exception as cleanup_error:
                        detail = f"Patch export cleanup failed: temporary index {temporary_index} in container {self.container_id}: {cleanup_error}"
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
            if self.state is DockerWorkspaceState.DESTROYED:
                return
            await self.backend._remove_container(self.container_id)
            self.state = DockerWorkspaceState.DESTROYED

    def _resolve_cwd(self, cwd: str) -> str:
        base = PurePosixPath(self.spec.options.workdir)
        requested = PurePosixPath(cwd)
        candidate = requested if requested.is_absolute() else base / requested
        resolved = PurePosixPath(posixpath.normpath(str(candidate)))
        if not resolved.is_relative_to(base):
            raise ValueError("Command working directory escapes the workspace")
        return str(resolved)

    def _require_active(self) -> None:
        if self.state is not DockerWorkspaceState.ACTIVE:
            raise DockerWorkspaceError(
                f"Docker workspace is not active: {self.state.value}"
            )

    def _require_not_destroyed(self) -> None:
        if self.state is DockerWorkspaceState.DESTROYED:
            raise DockerWorkspaceError("Docker workspace has been destroyed")
