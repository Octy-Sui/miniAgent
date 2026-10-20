from dataclasses import dataclass, field
from time import monotonic
from typing import Literal
from uuid import UUID

from miniagent.artifacts.contracts import ArtifactStore, ArtifactWrite
from miniagent.artifacts.writer import ArtifactWriter
from miniagent.environments.evidence import CommandOutputEvidence, StreamEvidence
from miniagent.environments.managed_execution import current_cleanup_deadline


@dataclass(slots=True)
class OutputCapture:
    attempt_id: UUID
    writers: dict[str, ArtifactWriter]
    output: CommandOutputEvidence | None = None
    received: dict[str, int] = field(default_factory=lambda: {"stdout": 0, "stderr": 0})

    @classmethod
    async def open(
        cls,
        *,
        store: ArtifactStore,
        run_id: UUID,
        attempt_id: UUID,
        deadline: float | None,
    ) -> "OutputCapture":
        writers: dict[str, ArtifactWriter] = {}
        try:
            for stream in ("stdout", "stderr"):
                writers[stream] = await store.open_writer(
                    ArtifactWrite(
                        run_id=run_id,
                        type=f"command_{stream}",
                        metadata={"attempt_id": str(attempt_id)},
                    ),
                    deadline=deadline,
                )
        except BaseException:
            for writer in writers.values():
                await writer.abort()
            raise
        return cls(attempt_id=attempt_id, writers=writers)

    async def write(self, stream: Literal["stdout", "stderr"], chunk: bytes) -> None:
        deadline = current_cleanup_deadline()
        if deadline is not None and monotonic() >= deadline:
            raise TimeoutError("Output persistence deadline exceeded")
        self.received[stream] += len(chunk)
        await self.writers[stream].write(chunk)

    async def finish(
        self,
        *,
        execution_id: UUID,
        stdout_eof: bool,
        stderr_eof: bool,
        reason: str | None,
    ) -> CommandOutputEvidence:
        streams = {}
        try:
            for stream, eof in (("stdout", stdout_eof), ("stderr", stderr_eof)):
                ref = await self.writers[stream].finish(
                    deadline=current_cleanup_deadline()
                )
                streams[stream] = StreamEvidence(
                    ref=ref,
                    received_bytes=self.received[stream],
                    status="complete" if eof else "partial",
                    eof_observed=eof,
                    reason=None if eof else reason or "stream drain stopped before EOF",
                )
        finally:
            await self.abort()
        self.output = CommandOutputEvidence(
            attempt_id=self.attempt_id,
            execution_id=execution_id,
            stdout=streams["stdout"],
            stderr=streams["stderr"],
        )
        return self.output

    async def abort(self) -> None:
        for writer in self.writers.values():
            await writer.abort()
