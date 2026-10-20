import asyncio
import hashlib
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from miniagent.artifacts.contracts import (
    ArtifactPersistenceError,
    ArtifactRecord,
    ArtifactRef,
    ArtifactWrite,
)


class ArtifactWriter(Protocol):
    async def write(self, chunk: bytes) -> None: ...

    async def finish(self, *, deadline: float | None = None) -> ArtifactRef: ...

    async def abort(self) -> None: ...


async def _settle[T](action: Callable[[], T]) -> tuple[T, bool]:
    task = asyncio.create_task(asyncio.to_thread(action))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    return task.result(), cancelled


class FileArtifactWriter:
    def __init__(
        self,
        root: Path,
        request: ArtifactWrite,
        deadline: float | None,
        publish_manifest: Callable[[Path, bytes], None],
    ) -> None:
        self._root = root
        self._request = request
        self._deadline = deadline
        self._publish_manifest = publish_manifest
        self._descriptor: int | None = None
        self._temporary: Path | None = None
        self._digest = hashlib.sha256()
        self._size = 0
        self._closed = False
        self._busy = False

    def _check_deadline(self) -> None:
        if (
            self._deadline is not None
            and asyncio.get_running_loop().time() >= self._deadline
        ):
            raise TimeoutError("Artifact persistence deadline exceeded")

    async def open(self) -> None:
        self._check_deadline()

        def create() -> None:
            directory = self._root / "staging"
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(dir=directory, suffix=".tmp")
            self._descriptor = descriptor
            self._temporary = Path(name)

        try:
            _, cancelled = await _settle(create)
            if cancelled:
                await self.abort()
                raise asyncio.CancelledError
        except OSError as exc:
            raise ArtifactPersistenceError(
                "Unable to persist artifact staging file"
            ) from exc

    def _enter(self) -> None:
        if self._closed:
            raise RuntimeError("Artifact writer is closed")
        if self._busy:
            raise RuntimeError("Artifact writer requires a single writer")
        self._check_deadline()
        self._busy = True

    async def write(self, chunk: bytes) -> None:
        self._enter()
        try:
            for offset in range(0, len(chunk), 65536):
                self._check_deadline()
                part = chunk[offset : offset + 65536]

                def write_part(part: bytes = part) -> None:
                    assert self._descriptor is not None
                    remaining = memoryview(part)
                    while remaining:
                        written = os.write(self._descriptor, remaining)
                        if written == 0:
                            raise OSError("Artifact write made no progress")
                        remaining = remaining[written:]

                _, cancelled = await _settle(write_part)
                self._digest.update(part)
                self._size += len(part)
                if cancelled:
                    raise asyncio.CancelledError
        except TimeoutError:
            raise
        except OSError as exc:
            raise ArtifactPersistenceError("Unable to persist artifact body") from exc
        finally:
            self._busy = False

    async def finish(self, *, deadline: float | None = None) -> ArtifactRef:
        if deadline is not None and not self._closed and not self._busy:
            self._deadline = (
                min(self._deadline, deadline)
                if self._deadline is not None
                else deadline
            )
        self._enter()
        self._closed = True
        digest = self._digest.hexdigest()
        target = self._root / "content" / digest[:2] / digest
        record = ArtifactRecord(
            artifact_id=uuid4(),
            run_id=self._request.run_id,
            type=self._request.type,
            name=self._request.name,
            media_type=self._request.media_type,
            metadata=self._request.metadata,
            uri=target.as_uri(),
            size_bytes=self._size,
            sha256=digest,
        )

        def publish_body() -> None:
            assert self._descriptor is not None and self._temporary is not None
            os.fsync(self._descriptor)
            os.close(self._descriptor)
            self._descriptor = None
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(self._temporary, target)

        try:
            _, cancelled = await _settle(publish_body)
            if cancelled:
                raise asyncio.CancelledError
            self._check_deadline()
            manifest = (
                self._root
                / "manifests"
                / record.run_id.hex
                / f"{record.artifact_id}.json"
            )
            # Once registration starts, its settled outcome takes priority over cancellation.
            await _settle(
                lambda: self._publish_manifest(
                    manifest, record.model_dump_json().encode("utf-8")
                )
            )
            return record.reference()
        except TimeoutError:
            raise
        except OSError as exc:
            raise ArtifactPersistenceError(
                "Unable to persist artifact publication"
            ) from exc
        finally:
            self._busy = False
            await self.abort()

    async def abort(self) -> None:
        if self._busy:
            raise RuntimeError("Artifact writer requires a single writer")
        self._closed = True

        def cleanup() -> None:
            if self._descriptor is not None:
                os.close(self._descriptor)
                self._descriptor = None
            if self._temporary is not None:
                self._temporary.unlink(missing_ok=True)

        try:
            await _settle(cleanup)
        except OSError as exc:
            raise ArtifactPersistenceError(
                "Unable to persist artifact cleanup"
            ) from exc
