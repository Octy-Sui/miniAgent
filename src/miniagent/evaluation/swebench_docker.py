import asyncio
import json
import re
from pathlib import Path

from miniagent.environments import docker_connection as _connection
from miniagent.environments.docker_connection import (
    DockerConnection as EvaluationDockerConnection,
)
from miniagent.environments.docker_connection import (
    docker_argv,
    harness_environment,
)
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


async def cleanup_containers(
    executable: Path,
    connection: EvaluationDockerConnection,
    *,
    instance_id: str,
    harness_run_id: str,
    deadline: float,
) -> tuple[list[dict[str, str]], list[str]]:
    loop = asyncio.get_running_loop()
    identities: list[dict[str, str]] = []
    diagnostics: list[str] = []
    pattern = re.compile(
        rf"^sweb\.eval\.{re.escape(instance_id.lower())}\.{re.escape(harness_run_id)}(?:\.[0-9]+)?$"
    )

    async def command(args: list[str]) -> bytes:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError("Container cleanup deadline exhausted")
        code, stdout, stderr = await _docker_command(
            executable, connection, args, timeout_seconds=min(10.0, remaining)
        )
        if code:
            raise ValueError(
                f"Docker cleanup failed: {stderr.decode(errors='replace')[:4096]}"
            )
        return stdout

    try:
        async with asyncio.timeout_at(deadline):
            await check_daemon(
                executable,
                connection,
                timeout_seconds=min(10.0, max(0.001, deadline - loop.time())),
            )
            listing = [
                json.loads(line)
                for line in (
                    await command(
                        [
                            "container",
                            "ls",
                            "--all",
                            "--no-trunc",
                            "--format",
                            "{{json .}}",
                        ]
                    )
                ).splitlines()
            ]
            for row in listing:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("Names"), str)
                    or not isinstance(row.get("ID"), str)
                ):
                    raise TypeError("Invalid container listing")
                if not pattern.fullmatch(row["Names"]):
                    continue
                detail = json.loads(await command(["container", "inspect", row["ID"]]))
                if not isinstance(detail, list) or len(detail) != 1:
                    raise ValueError("Container identity unavailable")
                item = detail[0]
                if any(
                    not isinstance(item.get(key), str)
                    for key in ("Id", "Name", "Image")
                ) or not pattern.fullmatch(item["Name"].lstrip("/")):
                    raise ValueError("Container inspected identity differs")
                identities.append(
                    {"id": item["Id"], "name": item["Name"], "image_id": item["Image"]}
                )
            for identity in identities:
                try:
                    await command(["container", "rm", "--force", identity["id"]])
                except (ValueError, EvaluationPreparationError) as exc:
                    diagnostics.append(
                        f"Uncleaned container {identity['id']} {identity['name']}: {exc}"
                    )
            for line in (
                await command(
                    ["container", "ls", "--all", "--no-trunc", "--format", "{{json .}}"]
                )
            ).splitlines():
                row = json.loads(line)
                if pattern.fullmatch(row["Names"]):
                    diagnostics.append(
                        f"Uncleaned container {row['ID']} {row['Names']}"
                    )
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as exc:
        diagnostics.append(
            f"Cleanup of sweb.eval.{instance_id.lower()}.{harness_run_id} not confirmed: {exc}"
        )
        diagnostics.extend(getattr(exc, "__notes__", ()))
    return identities, diagnostics
