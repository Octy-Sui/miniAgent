import hashlib
import multiprocessing
from pathlib import Path
from uuid import UUID

import pytest

from miniagent.artifacts.contracts import ArtifactRecord, ArtifactWrite
from miniagent.artifacts.file import FileArtifactStore
from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.reads import ReadBudget, ReadFailure
from tests.dashboard.observation import probe_content


def supervise(root: Path, run_id: UUID, artifact_id: UUID):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=probe_content, args=(str(root), str(run_id), str(artifact_id), writer)
    )
    process.start()
    writer.close()
    try:
        assert reader.poll(15), "product did not finish before supervisor deadline"
        result = reader.recv()
        process.join(1)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        reader.close()


def test_actual_oversize_reads_no_content(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    with staging.open("wb") as stream:
        stream.truncate(20 * 1024 * 1024 + 1)
    with staging.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target = tmp_path / "content" / digest[:2] / digest
    target.parent.mkdir(parents=True)
    staging.rename(target)
    record = ArtifactRecord(
        artifact_id=UUID(int=2),
        run_id=UUID(int=1),
        type="evaluation_stdout",
        media_type="text/plain",
        uri=target.as_uri(),
        size_bytes=20 * 1024 * 1024 - 1,
        sha256=digest,
    )
    manifest = tmp_path / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(record.model_dump_json())
    result = supervise(tmp_path, record.run_id, record.artifact_id)
    assert result["content"] is None
    assert result["reads"] == []
    assert result["hashes"] == []
    assert result["leaked"] == []
    assert "read_limit_exceeded" in result["issues"]


async def test_content_identity_and_layout(tmp_path: Path) -> None:
    content = b"visible-content" * (10000)
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        content,
    )
    result = supervise(tmp_path, UUID(int=1), ref.artifact_id)
    assert result["content"] == content
    assert result["reads"] and result["hashes"]
    assert all(size is not None and size <= 65536 for _, size in result["reads"])
    assert result["leaked"] == []


@pytest.mark.parametrize(("outside",), [(False,), (True,)])
@pytest.mark.parametrize(("node",), [("content",), ("prefix",), ("directory",)])
async def test_internal_symlinks_are_always_rejected(
    tmp_path: Path, node: str, outside: bool
) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        b"visible",
    )
    assert ref.sha256 is not None
    target = tmp_path / "content" / ref.sha256[:2] / ref.sha256
    if node == "prefix":
        target = target.parent
    elif node == "directory":
        target = tmp_path / "content"
    replacement = (
        tmp_path.parent / (tmp_path.name + "-outside")
        if outside
        else tmp_path / "saved"
    )
    target.rename(replacement)
    target.symlink_to(replacement, target_is_directory=node != "content")
    with pytest.raises(ReadFailure) as error:
        ArtifactReader(tmp_path).read_content(
            UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
        )
    assert error.value.status_code == 409


import json


@pytest.mark.parametrize(
    ("suffix",), [("?",), ("#",), ("?query=secret",), ("#fragment",)]
)
async def test_uri_query_and_fragment_are_rejected(tmp_path: Path, suffix: str) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        b"visible",
    )
    path = tmp_path / "manifests" / UUID(int=1).hex / f"{ref.artifact_id}.json"
    value = json.loads(path.read_text())
    value["uri"] += suffix
    path.write_text(json.dumps(value))
    with pytest.raises(ReadFailure) as error:
        ArtifactReader(tmp_path).read_content(
            UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    ("uri",),
    [
        ("file://host/content",),
        ("https://example.invalid/never-fetch",),
        ("file:///outside",),
        ("file:///bad%ZZ",),
        ("file:///bad%FF",),
        ("file:///bad%00",),
    ],
)
async def test_content_uri_cannot_select_an_arbitrary_path(
    tmp_path: Path, uri: str
) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        b"visible",
    )
    path = tmp_path / "manifests" / UUID(int=1).hex / f"{ref.artifact_id}.json"
    value = json.loads(path.read_text())
    value["uri"] = uri
    path.write_text(json.dumps(value))
    with pytest.raises(ReadFailure) as error:
        ArtifactReader(tmp_path).read_content(
            UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize(("recorded_size",), [(11,), (12,)])
async def test_size_comparison_for_bounded_content(
    tmp_path: Path, recorded_size: int
) -> None:
    content = b"012345678901"
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        content,
    )
    path = tmp_path / "manifests" / UUID(int=1).hex / f"{ref.artifact_id}.json"
    value = json.loads(path.read_text())
    value["size_bytes"] = recorded_size
    path.write_text(json.dumps(value))
    if recorded_size == 12:
        assert (
            ArtifactReader(tmp_path)
            .read_content(
                UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
            )
            .content
            == content
        )
    else:
        with pytest.raises(ReadFailure, match="artifact_size_mismatch"):
            ArtifactReader(tmp_path).read_content(
                UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
            )


async def test_reference_missing_fields_are_unknown_but_recorded_conflicts_fail(
    tmp_path: Path,
) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        b"visible",
    )
    partial = ref.model_copy(
        update={"size_bytes": None, "sha256": None, "type": None, "media_type": None}
    )
    reader = ArtifactReader(tmp_path)
    assert (
        reader.read_content(
            UUID(int=1),
            ref.artifact_id,
            expected=partial,
            budget=ReadBudget(5, monotonic=lambda: 0),
        ).content
        == b"visible"
    )
    with pytest.raises(ReadFailure, match="artifact_reference_mismatch"):
        reader.read_content(
            UUID(int=1),
            ref.artifact_id,
            expected=ref.model_copy(update={"size_bytes": 99}),
            budget=ReadBudget(5, monotonic=lambda: 0),
        )


@pytest.mark.parametrize(
    ("damage",), [("hash",), ("missing",), ("directory",), ("middle-file",)]
)
async def test_content_faults_do_not_become_verified_bytes(
    tmp_path: Path, damage: str
) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
        ),
        b"visible",
    )
    assert ref.sha256 is not None
    target = tmp_path / "content" / ref.sha256[:2] / ref.sha256
    if damage == "hash":
        target.write_bytes(b"VISIBLE")
    else:
        target.unlink()
        if damage == "directory":
            target.mkdir()
        elif damage == "middle-file":
            target.parent.rmdir()
            target.parent.write_bytes(b"not a directory")
    with pytest.raises(ReadFailure) as error:
        ArtifactReader(tmp_path).read_content(
            UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    ("size",), [(20971518,), (20971519,), (20971520,), (20971521,), (20971522,)]
)
def test_actual_file_size_boundary(tmp_path: Path, size: int) -> None:
    staging = tmp_path / "staging"
    with staging.open("wb") as stream:
        stream.truncate(size)
    with staging.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target = tmp_path / "content" / digest[:2] / digest
    target.parent.mkdir(parents=True)
    staging.rename(target)
    record = ArtifactRecord(
        artifact_id=UUID(int=2),
        run_id=UUID(int=1),
        type="evaluation_stdout",
        media_type="text/plain",
        uri=target.as_uri(),
        size_bytes=size,
        sha256=digest,
    )
    manifest = tmp_path / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(record.model_dump_json())
    result = supervise(tmp_path, record.run_id, record.artifact_id)
    if size <= 20971520:
        assert len(result["content"]) == size
        assert result["reads"] and result["hashes"]
    else:
        assert result["content"] is None
        assert result["reads"] == result["hashes"] == []
    assert result["leaked"] == []
