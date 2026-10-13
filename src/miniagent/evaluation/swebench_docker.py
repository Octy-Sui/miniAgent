import asyncio
import json
import os
import re
import signal
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from miniagent.evaluation.swebench_data import EvaluationPreparationError


@dataclass(frozen=True, slots=True)
class EvaluationDockerConnection:
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
    deadline = loop.time() + timeout_seconds
    reserve = min(1.0, timeout_seconds / 4)
    process: asyncio.subprocess.Process | None = None
    readers: list[asyncio.Task[bytes]] = []
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
            stdout, stderr = await asyncio.gather(*readers)
            return await process.wait(), stdout, stderr
    finally:
        if process is not None and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout_at(deadline):
                    await asyncio.shield(process.wait())
            except TimeoutError as exc:
                exc.add_note(f"CLI PID {process.pid} was not confirmed reaped")
                raise
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.wait(readers, timeout=max(0.0, deadline - loop.time()))


def harness_environment(
    environment: dict[str, str], connection: EvaluationDockerConnection
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
    executable: Path, connection: EvaluationDockerConnection, args: list[str]
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
    connection: EvaluationDockerConnection,
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
        raise EvaluationPreparationError(
            "DOCKER_CONNECTION_INVALID", f"{connection.endpoint}: {exc}"
        ) from exc


async def check_daemon(
    executable: Path,
    connection: EvaluationDockerConnection,
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
        raise EvaluationPreparationError(
            "DOCKER_CONNECTION_INVALID",
            f"Daemon ID unavailable: {stderr.decode(errors='replace')[:4096]}",
        )
    if daemon_id != connection.daemon_id:
        raise EvaluationPreparationError(
            "DOCKER_DAEMON_CHANGED",
            f"Expected {connection.daemon_id}, observed {daemon_id}",
        )


async def inspect_image(
    executable: Path,
    connection: EvaluationDockerConnection,
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
        raise EvaluationPreparationError(
            "DOCKER_CONNECTION_INVALID",
            f"Image inspect failed: {stderr.decode(errors='replace')[:4096]}",
        )
    image_id = stdout.decode().strip()
    if not image_id:
        raise EvaluationPreparationError(
            "DOCKER_CONNECTION_INVALID", "Image ID unavailable"
        )
    return image_id


async def resolve_docker_connection(
    executable: Path, environment: dict[str, str]
) -> EvaluationDockerConnection:
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
                raise EvaluationPreparationError(
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
                raise EvaluationPreparationError(
                    "DOCKER_CONNECTION_UNSUPPORTED", "Unverified TLS environment"
                )
            if environment.get("DOCKER_TLS") and not tls:
                raise EvaluationPreparationError(
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
            raise EvaluationPreparationError("DOCKER_CONNECTION_UNSUPPORTED", endpoint)
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
        connection = EvaluationDockerConnection(
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
        if isinstance(exc, EvaluationPreparationError):
            raise
        raise EvaluationPreparationError("DOCKER_CONNECTION_INVALID", str(exc)) from exc


async def verify_harness_version(executable: Path) -> str:
    try:
        code, stdout, stderr = await _run_command(
            [str(executable), "--version"], environment={}, timeout_seconds=10.0
        )
        if code or not re.search(rb"(?<![\d.])5\.0\.2(?![\d.])", stdout + stderr):
            raise ValueError("Expected swebench 5.0.2")
        return "5.0.2"
    except (OSError, TimeoutError, ValueError) as exc:
        raise EvaluationPreparationError(
            "HARNESS_VERSION_MISMATCH", f"{executable}: {exc}"
        ) from exc
