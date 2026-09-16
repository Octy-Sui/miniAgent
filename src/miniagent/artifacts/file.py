# File   : file.py
# Created: 2026-09-16 16:45:06
# Author : octy Sui

import asyncio
import hashlib
from pathlib import Path
from urllib.parse import unquote, urlparse

from miniagent.artifacts.store import (
    ArtifactNotFoundError,
    ArtifactWrite,
)
from miniagent.models.artifact import ArtifactRef


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    async def put(self, request: ArtifactWrite, content: bytes) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        name = Path(request.name or digest).name
        target = self.root / request.run_id.hex / request.type / f"{digest}-{name}"
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, content)
        return ArtifactRef(
            uri=target.as_uri(),
            type=request.type,
            media_type=request.media_type,
            size_bytes=len(content),
            sha256=digest,
        )

    async def read(self, ref: ArtifactRef) -> bytes:
        parsed = urlparse(ref.uri)
        if parsed.scheme != "file":
            raise ArtifactNotFoundError(f"Unsupported artifact URI: {ref.uri}")
        target = Path(unquote(parsed.path)).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {ref.uri}")
        content = await asyncio.to_thread(target.read_bytes)
        if ref.sha256 is not None and hashlib.sha256(content).hexdigest() != ref.sha256:
            raise ArtifactNotFoundError(f"Artifact content changed: {ref.uri}")
        return content
