import hashlib
from pathlib import Path
from uuid import UUID

from miniagent.artifacts.contracts import ArtifactRecord


def manifest(
    root: Path, number: int, kind: str = "evaluation_stdout"
) -> ArtifactRecord:
    content = b"visible-content"
    digest = hashlib.sha256(content).hexdigest()
    target = root / "content" / digest[:2] / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    record = ArtifactRecord(
        artifact_id=UUID(int=number),
        run_id=UUID(int=1),
        type=kind,
        name=f"artifact-{number}",
        media_type="text/plain",
        uri=target.as_uri(),
        size_bytes=len(content),
        sha256=digest,
    )
    destination = root / "manifests" / UUID(int=1).hex / f"{record.artifact_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(record.model_dump_json())
    return record
