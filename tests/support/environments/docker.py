from uuid import uuid4

from miniagent.artifacts import ArtifactStore, ArtifactWrite
from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments import (
    DockerOptions,
    DockerWorkspaceBackend,
    DockerWorkspaceSpec,
    ResourceLimits,
)
from miniagent.environments.docker import DockerCommandResult


class UnusedArtifactStore(ArtifactStore):
    async def put(
        self, request: ArtifactWrite, content: bytes, *, deadline: float | None = None
    ) -> ArtifactRef:
        raise AssertionError("Artifact store should not be used")

    async def read(self, ref: ArtifactRef) -> bytes:
        raise AssertionError("Artifact store should not be used")


class ScriptedDockerBackend(DockerWorkspaceBackend):
    def __init__(self, responses: list[DockerCommandResult | BaseException]) -> None:
        super().__init__(artifact_store=UnusedArtifactStore())
        self.responses = responses
        self.commands: list[list[str]] = []

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
        self.commands.append(argv)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def docker_result(
    stdout: bytes = b"",
    *,
    exit_code: int = 0,
) -> DockerCommandResult:
    return DockerCommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=b"",
        duration_ms=1,
    )


def workspace_spec() -> DockerWorkspaceSpec:
    return DockerWorkspaceSpec(
        run_id=uuid4(),
        options=DockerOptions(
            image="swebench/task:latest",
            workdir="/testbed",
            limits=ResourceLimits(cpu_count=1.5, memory_mb=768, pids=64),
        ),
    )
