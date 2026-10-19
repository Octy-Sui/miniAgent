import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.environments.managed_execution import (
    ManagedExecutionError,
    current_cleanup_deadline,
    current_execution_observer,
)
from miniagent.environments.process import (
    identify_host_process,
    inspect_host_execution,
    process_group_absent,
    reap_process,
    register_host_process,
)


class DockerConnectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class DockerConnection:
    source_context: str | None
    endpoint: str
    cert_path: Path | None
    tls_verify: bool
    daemon_id: str
    client_environment: tuple[tuple[str, str], ...] = ()


async def _read_output(stream: asyncio.StreamReader) -> bytes:
    output = bytearray()
    while chunk := await stream.read(65536):
        if len(output) + len(chunk) > 262144:
            raise ValueError("Docker preparation output exceeds 262144 bytes")
        output.extend(chunk)
    return bytes(output)


async def _run_command(
    argv: list[str], *, environment: dict[str, str], timeout_seconds: float
) -> tuple[int, bytes, bytes]:
    loop = asyncio.get_running_loop()
    deadline = min(
        loop.time() + timeout_seconds, current_cleanup_deadline() or float("inf")
    )
    if deadline <= loop.time():
        raise TimeoutError("Docker command deadline exhausted before launch")
    reserve = min(1.0, max(0.0, deadline - loop.time()) / 4)
    process = None
    readers = []
    identity = None
    observer = current_execution_observer()
    if observer is not None:
        observer.check_permission()
    try:
        async with asyncio.timeout_at(deadline - reserve):
            process = await asyncio.create_subprocess_exec(
                *argv,
                env=environment,
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None and process.stderr is not None
            readers = [
                asyncio.create_task(_read_output(process.stdout)),
                asyncio.create_task(_read_output(process.stderr)),
            ]
            try:
                identity = identify_host_process(
                    process.pid,
                    attempt_id=observer.attempt_id if observer else uuid4(),
                    execution_id=uuid4(),
                    role="docker_helper",
                )
            except ManagedExecutionError:
                if observer is not None or not process_group_absent(process.pid):
                    raise
            if identity is not None:
                register_host_process(process, identity)
                if observer:
                    await observer.identified(identity)
            stdout, stderr = await asyncio.gather(*readers)
            code = await process.wait()
            if identity is not None:
                evidence = inspect_host_execution(identity)
                if evidence.outcome != "ended":
                    raise ManagedExecutionError(
                        "Docker CLI whole group end is unconfirmed"
                    )
                if observer:
                    await observer.ended(evidence)
            return code, stdout, stderr
    except BaseException as exc:
        if process is not None:
            diagnostics = await reap_process(process, list(readers), deadline=deadline)
            if diagnostics:
                raise ManagedExecutionError(
                    "Docker CLI cleanup is unconfirmed"
                ) from exc
            if (
                isinstance(exc, (TimeoutError, asyncio.CancelledError))
                and identity is not None
                and observer is not None
            ):
                await observer.ended(inspect_host_execution(identity))
        raise


def harness_environment(
    environment: dict[str, str], connection: DockerConnection
) -> dict[str, str]:
    result = {
        key: value
        for key, value in environment.items()
        if key
        not in {
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        }
    }
    result["DOCKER_HOST"] = connection.endpoint
    if connection.tls_verify:
        result["DOCKER_TLS_VERIFY"] = "1"
        result["DOCKER_CERT_PATH"] = str(connection.cert_path)
    return result


def docker_argv(
    executable: Path, connection: DockerConnection, args: list[str]
) -> list[str]:
    argv = [str(executable), "--host", connection.endpoint]
    if connection.tls_verify:
        assert connection.cert_path is not None
        argv += [
            "--tlsverify",
            "--tlscacert",
            str(connection.cert_path / "ca.pem"),
            "--tlscert",
            str(connection.cert_path / "cert.pem"),
            "--tlskey",
            str(connection.cert_path / "key.pem"),
        ]
    return [*argv, *args]


async def _docker_command(
    executable: Path,
    connection: DockerConnection,
    args: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int, bytes, bytes]:
    try:
        return await _run_command(
            docker_argv(executable, connection, args),
            environment=harness_environment(
                dict(connection.client_environment), connection
            ),
            timeout_seconds=timeout_seconds,
        )
    except (OSError, TimeoutError, ValueError) as exc:
        raise DockerConnectionError(
            "DOCKER_CONNECTION_INVALID", f"{connection.endpoint}: {exc}"
        ) from exc


async def check_daemon(
    executable: Path,
    connection: DockerConnection,
    *,
    timeout_seconds: float = 10.0,
) -> None:
    code, stdout, stderr = await _docker_command(
        executable,
        connection,
        ["info", "--format", "{{.ID}}"],
        timeout_seconds=timeout_seconds,
    )
    daemon_id = stdout.decode().strip()
    if code or not daemon_id:
        raise DockerConnectionError(
            "DOCKER_CONNECTION_INVALID",
            f"Daemon ID unavailable: {stderr.decode(errors='replace')[:4096]}",
        )
    if daemon_id != connection.daemon_id:
        raise DockerConnectionError(
            "DOCKER_DAEMON_CHANGED",
            f"Expected {connection.daemon_id}, observed {daemon_id}",
        )


async def inspect_image(
    executable: Path,
    connection: DockerConnection,
    image: str,
    *,
    timeout_seconds: float = 10.0,
) -> str | None:
    code, stdout, stderr = await _docker_command(
        executable,
        connection,
        ["image", "inspect", "--format", "{{.Id}}", image],
        timeout_seconds=timeout_seconds,
    )
    if code:
        if b"No such image:" in stderr or b"No such object:" in stderr:
            return None
        raise DockerConnectionError(
            "DOCKER_CONNECTION_INVALID",
            f"Image inspect failed: {stderr.decode(errors='replace')[:4096]}",
        )
    image_id = stdout.decode().strip()
    if not image_id:
        raise DockerConnectionError("DOCKER_CONNECTION_INVALID", "Image ID unavailable")
    return image_id


async def resolve_docker_connection(
    executable: Path, environment: dict[str, str]
) -> DockerConnection:
    context = environment.get("DOCKER_CONTEXT") or None
    endpoint = environment.get("DOCKER_HOST") or None
    cert_path: Path | None = None
    tls = False
    try:
        if context is not None or endpoint is None:
            if context is None:
                code, stdout, _ = await _run_command(
                    [str(executable), "context", "show"],
                    environment=environment,
                    timeout_seconds=10.0,
                )
                context = stdout.decode().strip()
                if code or not context:
                    raise ValueError("Current Docker context unavailable")
            code, stdout, _ = await _run_command(
                [str(executable), "context", "inspect", context],
                environment=environment,
                timeout_seconds=10.0,
            )
            document = json.loads(stdout)
            if code or not isinstance(document, list) or len(document) != 1:
                raise ValueError("Invalid Docker context")
            config = document[0]
            docker = config["Endpoints"]["docker"]
            endpoint = docker["Host"]
            if docker.get("SkipTLSVerify") is True:
                raise DockerConnectionError(
                    "DOCKER_CONNECTION_UNSUPPORTED", "Unverified TLS context"
                )
            material = config.get("TLSMaterial", {}).get("docker", [])
            if material:
                if set(material) != {"ca.pem", "cert.pem", "key.pem"}:
                    raise ValueError("Context lacks TLS material")
                cert_path = Path(config["Storage"]["TLSPath"]) / "docker"
                tls = True
        else:
            tls = bool(
                environment.get("DOCKER_CERT_PATH")
                or environment.get("DOCKER_TLS_VERIFY")
            )
            if tls and not environment.get("DOCKER_TLS_VERIFY"):
                raise DockerConnectionError(
                    "DOCKER_CONNECTION_UNSUPPORTED", "Unverified TLS environment"
                )
            if environment.get("DOCKER_TLS") and not tls:
                raise DockerConnectionError(
                    "DOCKER_CONNECTION_UNSUPPORTED", "DOCKER_TLS alone is unsupported"
                )
            if tls:
                cert_path = Path(
                    environment.get("DOCKER_CERT_PATH")
                    or str(Path(environment.get("HOME", str(Path.home()))) / ".docker")
                )
        if not isinstance(endpoint, str) or not endpoint:
            raise ValueError("Missing Docker endpoint")
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"unix", "tcp"}:
            raise DockerConnectionError("DOCKER_CONNECTION_UNSUPPORTED", endpoint)
        if parsed.scheme == "unix":
            if tls or not parsed.path or parsed.netloc:
                raise ValueError("Invalid Unix endpoint")
            endpoint = f"unix://{Path(parsed.path).expanduser().resolve()}"
        elif (
            not parsed.hostname or parsed.port is None or parsed.username or parsed.path
        ):
            raise ValueError("Invalid TCP endpoint")
        if tls:
            assert cert_path is not None
            cert_path = cert_path.expanduser().resolve()
            for name in ("ca.pem", "cert.pem", "key.pem"):
                with (cert_path / name).open("rb") as stream:
                    if not stream.read(1):
                        raise ValueError("Empty TLS material")
        connection = DockerConnection(
            context,
            endpoint,
            cert_path,
            tls,
            "",
            tuple(
                (key, environment[key])
                for key in ("HOME", "PATH", "DOCKER_CONFIG")
                if key in environment
            ),
        )
        code, stdout, stderr = await _docker_command(
            executable,
            connection,
            ["info", "--format", "{{.ID}}"],
            timeout_seconds=10.0,
        )
        if code or not stdout.strip():
            raise ValueError(
                f"Daemon ID unavailable: {stderr.decode(errors='replace')[:4096]}"
            )
        return replace(connection, daemon_id=stdout.decode().strip())
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        if isinstance(exc, DockerConnectionError):
            raise
        raise DockerConnectionError("DOCKER_CONNECTION_INVALID", str(exc)) from exc


class FrozenDockerConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_context: str | None
    endpoint: str
    cert_path: Path | None
    tls_verify: bool
    daemon_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_connection(self) -> "FrozenDockerConnection":
        parsed = urlparse(self.endpoint)
        if parsed.scheme == "unix":
            if not parsed.path.startswith("/") or parsed.netloc or self.tls_verify:
                raise ValueError("Invalid Unix connection endpoint")
        elif parsed.scheme == "tcp":
            if (
                not parsed.hostname
                or parsed.port is None
                or parsed.username
                or parsed.path
            ):
                raise ValueError("Invalid TCP connection endpoint")
        else:
            raise ValueError("Unsupported connection endpoint")
        if self.tls_verify and (
            self.cert_path is None or not self.cert_path.is_absolute()
        ):
            raise ValueError("Verified connection requires absolute cert_path")
        if not self.tls_verify and self.cert_path is not None:
            raise ValueError("Non-TLS connection cannot use cert_path")
        return self


def bind_docker_connection(
    frozen: FrozenDockerConnection, environment: Mapping[str, str]
) -> DockerConnection:
    if frozen.tls_verify:
        assert frozen.cert_path is not None
        try:
            for name in ("ca.pem", "cert.pem", "key.pem"):
                with (frozen.cert_path / name).open("rb") as stream:
                    if not stream.read(1):
                        raise ValueError("Empty TLS reference")
        except (OSError, ValueError) as exc:
            raise DockerConnectionError(
                "ENVIRONMENT_REFERENCE_MISSING",
                "Frozen Docker TLS reference unavailable",
            ) from exc
    return DockerConnection(
        frozen.source_context,
        frozen.endpoint,
        frozen.cert_path,
        frozen.tls_verify,
        frozen.daemon_id,
        tuple(
            (key, environment[key])
            for key in ("HOME", "PATH", "DOCKER_CONFIG")
            if key in environment
        ),
    )
