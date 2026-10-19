import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from miniagent.artifacts import ArtifactRef
from miniagent.environments import docker_connection as _connection
from miniagent.environments.docker_connection import (
    DockerConnection as EvaluationDockerConnection,
)
from miniagent.environments.docker_connection import (
    docker_argv,
    harness_environment,
)
from miniagent.environments.managed_execution import (
    ExecutionEvidence,
    ExecutionIdentity,
    HostProcessIdentity,
    ManagedExecutionError,
)
from miniagent.environments.process import inspect_host_execution
from miniagent.evaluation.swebench_data import EvaluationPreparationError

__all__ = [
    "EvaluationDockerConnection",
    "check_daemon",
    "cleanup_containers",
    "docker_argv",
    "harness_environment",
    "inspect_image",
    "resolve_docker_connection",
    "verify_harness_version",
]

_run_command = _connection._run_command


async def _docker_command(
    executable: Path,
    connection: EvaluationDockerConnection,
    args: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, bytes, bytes]:
    try:
        return await _connection._docker_command(
            executable, connection, args, timeout_seconds=timeout_seconds
        )
    except _connection.DockerConnectionError as exc:
        raise EvaluationPreparationError(exc.code, str(exc)) from exc


async def check_daemon(
    executable: Path,
    connection: EvaluationDockerConnection,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    try:
        await _connection.check_daemon(
            executable, connection, timeout_seconds=timeout_seconds
        )
    except _connection.DockerConnectionError as exc:
        raise EvaluationPreparationError(exc.code, str(exc)) from exc


async def inspect_image(
    executable: Path,
    connection: EvaluationDockerConnection,
    image: str,
    *,
    timeout_seconds: float = 10.0,
) -> str | None:
    try:
        return await _connection.inspect_image(
            executable, connection, image, timeout_seconds=timeout_seconds
        )
    except _connection.DockerConnectionError as exc:
        raise EvaluationPreparationError(exc.code, str(exc)) from exc


async def resolve_docker_connection(
    executable: Path, environment: dict[str, str]
) -> EvaluationDockerConnection:
    try:
        return await _connection.resolve_docker_connection(executable, environment)
    except _connection.DockerConnectionError as exc:
        raise EvaluationPreparationError(exc.code, str(exc)) from exc


async def verify_harness_version(executable: Path) -> str:
    deadline = asyncio.get_running_loop().time() + 10.0
    try:
        code, stdout, stderr = await _run_command(
            [str(executable), "--version"], environment={}, timeout_seconds=10.0
        )
        if code and b"No such option: --version" in stderr:
            with executable.open("rb") as stream:
                header = stream.readline(4096).decode("utf-8").strip()
            if not header.startswith("#!/") or " " in header[2:]:
                raise ValueError("Cannot bind uv tool interpreter for version check")
            interpreter = header[2:]
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Version check budget exhausted")
            code, stdout, stderr = await _run_command(
                [
                    interpreter,
                    "-c",
                    "from importlib.metadata import version; print(version('swebench'))",
                ],
                environment={},
                timeout_seconds=remaining,
            )
            if code or stdout.strip() != b"5.0.2":
                raise ValueError("Expected bound swebench distribution 5.0.2")
        elif code or not re.search(rb"(?<![\d.])5\.0\.2(?![\d.])", stdout + stderr):
            raise ValueError("Expected swebench 5.0.2")
        return "5.0.2"
    except (OSError, TimeoutError, ValueError) as exc:
        raise EvaluationPreparationError(
            "HARNESS_VERSION_MISMATCH", f"{executable}: {exc}"
        ) from exc


class SWEbenchAttemptState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    attempt_id: UUID
    harness_run_id: str
    instance_id: str = Field(min_length=1)
    task_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_ref: ArtifactRef
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    directory: Path
    resource_instance_id: str
    resource_harness_run_id: str

    @model_validator(mode="after")
    def validate_identity(self) -> "SWEbenchAttemptState":
        if (
            not self.directory.is_absolute()
            or ".." in self.directory.parts
            or self.directory.name != self.attempt_id.hex
            or self.harness_run_id
            != f"{self.directory.parent.name}-{self.attempt_id.hex}"
            or not re.fullmatch(r"[0-9a-f]{32}", self.directory.parent.name)
            or self.resource_instance_id != self.instance_id
            or self.resource_harness_run_id != self.harness_run_id
        ):
            raise ValueError("Evaluation attempt identity or resource scope differs")
        return self


class EvaluationAttemptBusy(ManagedExecutionError):
    code = "EVALUATION_ATTEMPT_BUSY"


class EvaluationContainer(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    container_id: str = Field(alias="Id", pattern=r"^[0-9a-f]{64}$")
    name: str = Field(alias="Name")
    image_id: str = Field(alias="Image", min_length=1)

    def summary(self) -> dict[str, str]:
        return {"id": self.container_id, "name": self.name, "image_id": self.image_id}


class _ContainerListing(BaseModel):
    model_config = ConfigDict(strict=True)
    container_id: str = Field(alias="ID")
    names: str = Field(alias="Names")


@dataclass(frozen=True, slots=True)
class EvaluationAttemptFacts:
    attempt_id: UUID | None
    instance_id: str
    harness_run_id: str
    docker_executable: Path
    connection: EvaluationDockerConnection
    containers: tuple[EvaluationContainer, ...]
    host_ended: bool
    execution_evidence: tuple[ExecutionEvidence, ...] = ()
    errors: tuple[str, ...] = ()


def _resource_pattern(instance_id: str, harness_run_id: str) -> re.Pattern[str]:
    return re.compile(
        rf"^sweb\.eval\.{re.escape(instance_id.lower())}\.{re.escape(harness_run_id)}(?:\.[0-9]+)?$"
    )


async def _resource_command(
    executable: Path,
    connection: EvaluationDockerConnection,
    args: list[str],
    deadline: float,
) -> bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("Evaluation resource deadline exhausted")
    code, stdout, stderr = await _docker_command(
        executable, connection, args, timeout_seconds=min(10.0, remaining)
    )
    if code:
        raise ValueError(
            f"Docker resource inspection failed: {stderr.decode(errors='replace')[:4096]}"
        )
    return stdout


async def _inspect_containers(
    executable: Path,
    connection: EvaluationDockerConnection,
    instance_id: str,
    harness_run_id: str,
    deadline: float,
) -> tuple[tuple[EvaluationContainer, ...], tuple[str, ...]]:
    pattern = _resource_pattern(instance_id, harness_run_id)
    identities = []
    try:
        await check_daemon(
            executable,
            connection,
            timeout_seconds=min(
                10.0, max(0.001, deadline - asyncio.get_running_loop().time())
            ),
        )
        listing = await _resource_command(
            executable,
            connection,
            ["container", "ls", "--all", "--no-trunc", "--format", "{{json .}}"],
            deadline,
        )
        for line in listing.splitlines():
            row = _ContainerListing.model_validate_json(line)
            names = row.names.split(",")
            if not any(pattern.fullmatch(name.strip()) for name in names):
                continue
            if len(names) != 1 or not re.fullmatch(r"[0-9a-f]{64}", row.container_id):
                raise ValueError("Evaluation resource listing identity is ambiguous")
            detail = TypeAdapter(list[EvaluationContainer]).validate_json(
                await _resource_command(
                    executable,
                    connection,
                    ["container", "inspect", row.container_id],
                    deadline,
                )
            )
            if (
                len(detail) != 1
                or detail[0].container_id != row.container_id
                or detail[0].name.lstrip("/") != row.names
                or not pattern.fullmatch(detail[0].name.lstrip("/"))
            ):
                raise ValueError("Evaluation resource inspected identity differs")
            identities.append(detail[0])
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        return tuple(identities), (str(exc)[:4096],)
    return tuple(identities), ()


async def inspect_attempt_resources(
    attempt: SWEbenchAttemptState,
    connection: EvaluationDockerConnection,
    *,
    docker_executable: Path = Path("docker"),
    host_executions: tuple[ExecutionIdentity, ...] = (),
) -> EvaluationAttemptFacts:
    evidence = tuple(
        inspect_host_execution(identity)
        for identity in host_executions
        if isinstance(identity, HostProcessIdentity)
    )
    ended = (
        any(
            isinstance(identity, HostProcessIdentity) and identity.role == "harness"
            for identity in host_executions
        )
        and len(evidence) == len(host_executions)
        and all(
            item.outcome == "ended" and item.group_ended and item.issuer_ended
            for item in evidence
        )
    )
    if not ended:
        return EvaluationAttemptFacts(
            attempt.attempt_id,
            attempt.resource_instance_id,
            attempt.resource_harness_run_id,
            docker_executable,
            connection,
            (),
            False,
            evidence,
            ("Harness execution end is unconfirmed",),
        )
    containers, errors = await _inspect_containers(
        docker_executable,
        connection,
        attempt.resource_instance_id,
        attempt.resource_harness_run_id,
        asyncio.get_running_loop().time() + 10,
    )
    return EvaluationAttemptFacts(
        attempt.attempt_id,
        attempt.resource_instance_id,
        attempt.resource_harness_run_id,
        docker_executable,
        connection,
        containers,
        True,
        evidence,
        errors,
    )


async def cleanup_attempt_resources(
    facts: EvaluationAttemptFacts, *, deadline: float
) -> None:
    if not facts.host_ended or facts.errors:
        raise EvaluationAttemptBusy(
            "Evaluation attempt cleanup lacks verified ownership and harness end"
        )
    fresh, errors = await _inspect_containers(
        facts.docker_executable,
        facts.connection,
        facts.instance_id,
        facts.harness_run_id,
        deadline,
    )
    if errors or fresh != facts.containers:
        raise EvaluationAttemptBusy("Evaluation resources changed after inspection")
    for container in fresh:
        try:
            await _resource_command(
                facts.docker_executable,
                facts.connection,
                ["container", "rm", "--force", container.container_id],
                deadline,
            )
        except (OSError, ValueError, TimeoutError) as exc:
            raise EvaluationAttemptBusy(
                "Evaluation resource removal is unconfirmed"
            ) from exc
    remaining, errors = await _inspect_containers(
        facts.docker_executable,
        facts.connection,
        facts.instance_id,
        facts.harness_run_id,
        deadline,
    )
    if errors or remaining:
        raise EvaluationAttemptBusy("Evaluation resource cleanup is unconfirmed")


async def cleanup_containers(
    executable: Path,
    connection: EvaluationDockerConnection,
    *,
    instance_id: str,
    harness_run_id: str,
    deadline: float,
) -> tuple[list[dict[str, str]], list[str]]:
    containers, errors = await _inspect_containers(
        executable, connection, instance_id, harness_run_id, deadline
    )
    facts = EvaluationAttemptFacts(
        None,
        instance_id,
        harness_run_id,
        executable,
        connection,
        containers,
        True,
        errors=errors,
    )
    try:
        await cleanup_attempt_resources(facts, deadline=deadline)
    except (EvaluationAttemptBusy, OSError, ValueError, TimeoutError) as exc:
        return [container.summary() for container in containers], [
            *errors,
            str(exc)[:4096],
        ]
    return [container.summary() for container in containers], []
