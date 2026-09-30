import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

from pydantic import ValidationError

from miniagent.artifacts.contracts import (
    ArtifactNotFoundError,
    ArtifactRecord,
    ArtifactRef,
    ArtifactWrite,
)

_SAFE_TYPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.content_root = self.root / "content"
        self.manifest_root = self.root / "manifests"

    async def put(self, request: ArtifactWrite, content: bytes) -> ArtifactRef:
        self._validate_write(request)
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = uuid4()
        target = self.content_root / digest[:2] / digest
        record = ArtifactRecord(
            artifact_id=artifact_id,
            run_id=request.run_id,
            type=request.type,
            name=request.name,
            media_type=request.media_type,
            uri=target.as_uri(),
            size_bytes=len(content),
            sha256=digest,
            metadata=request.metadata,
        )
        manifest = self._manifest_path(request.run_id, artifact_id)
        await asyncio.to_thread(self._publish, target, content, manifest, record)
        return record.reference()

    async def read(self, ref: ArtifactRef) -> bytes:
        record = await self.get_record(ref)
        return await asyncio.to_thread(self._read_content, record)

    async def get_record(self, ref: ArtifactRef) -> ArtifactRecord:
        matches = await asyncio.to_thread(
            lambda: list(self.manifest_root.glob(f"*/{ref.artifact_id}.json"))
        )
        if len(matches) != 1:
            raise ArtifactNotFoundError(f"Artifact record not found: {ref.artifact_id}")
        record = await asyncio.to_thread(self._load_manifest, matches[0])
        if record.reference() != ref:
            raise ArtifactNotFoundError(
                f"Artifact reference does not match its record: {ref.artifact_id}"
            )
        await asyncio.to_thread(self._validate_content, record)
        return record

    async def list_for_run(self, run_id: UUID) -> list[ArtifactRecord]:
        directory = self.manifest_root / run_id.hex
        paths = await asyncio.to_thread(
            lambda: sorted(directory.glob("*.json")) if directory.is_dir() else []
        )
        records = [await asyncio.to_thread(self._load_manifest, path) for path in paths]
        if any(record.run_id != run_id for record in records):
            raise ArtifactNotFoundError("Artifact manifest is outside its recorded run")
        await asyncio.gather(
            *(asyncio.to_thread(self._validate_content, record) for record in records)
        )
        return records

    def _publish(
        self,
        target: Path,
        content: bytes,
        manifest: Path,
        record: ArtifactRecord,
    ) -> None:
        self._atomic_write(target, content)
        self._atomic_write(manifest, record.model_dump_json().encode("utf-8"))

    @staticmethod
    def _atomic_write(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _manifest_path(self, run_id: UUID, artifact_id: UUID) -> Path:
        return self.manifest_root / run_id.hex / f"{artifact_id}.json"

    def _content_path(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ArtifactNotFoundError(f"Unsupported artifact URI: {uri}")
        target = Path(unquote(parsed.path)).resolve()
        if not target.is_relative_to(self.content_root) or not target.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {uri}")
        return target

    def _validate_content(self, record: ArtifactRecord) -> None:
        self._read_content(record)

    def _read_content(self, record: ArtifactRecord) -> bytes:
        target = self._content_path(record.uri)
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise ArtifactNotFoundError(f"Artifact not found: {record.uri}") from exc
        if (
            len(content) != record.size_bytes
            or hashlib.sha256(content).hexdigest() != record.sha256
        ):
            raise ArtifactNotFoundError(f"Artifact content changed: {record.uri}")
        return content

    @staticmethod
    def _load_manifest(path: Path) -> ArtifactRecord:
        try:
            return ArtifactRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise ArtifactNotFoundError(
                f"Invalid artifact manifest: {path.name}"
            ) from exc

    @staticmethod
    def _validate_write(request: ArtifactWrite) -> None:
        if not _SAFE_TYPE.fullmatch(request.type):
            raise ValueError("Artifact type must be a safe path segment")
        if request.name is not None and (
            not request.name
            or Path(request.name).name != request.name
            or request.name in {".", ".."}
        ):
            raise ValueError("Artifact name must be a safe path segment")
