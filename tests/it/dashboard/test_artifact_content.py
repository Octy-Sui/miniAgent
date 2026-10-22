import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import pytest

from miniagent.artifacts.contracts import ArtifactRecord, ArtifactWrite
from miniagent.artifacts.file import FileArtifactStore
from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.reads import ReadBudget, ReadFailure
from tests.support.dashboard.artifact_content import supervise


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


@pytest.mark.parametrize("legacy", [False, True])
async def test_named_trajectory_content_remains_readable(tmp_path: Path, legacy: bool):
    store = FileArtifactStore(tmp_path)
    content = b'{"kind":"miniagent.trajectory"}'
    ref = await store.put(
        ArtifactWrite(
            run_id=UUID(int=1),
            type="run_trajectory",
            name="任务_model_2026-10-22.json",
            media_type="application/json",
        ),
        content,
    )
    if legacy:
        assert ref.sha256 is not None
        target = tmp_path / "content" / ref.sha256[:2] / ref.sha256
        target.parent.mkdir(parents=True, exist_ok=True)
        Path(unquote(urlsplit(ref.uri).path)).rename(target)
        manifest = tmp_path / "manifests" / UUID(int=1).hex / f"{ref.artifact_id}.json"
        value = json.loads(manifest.read_text())
        value["uri"] = target.as_uri()
        manifest.write_text(json.dumps(value))
    result = ArtifactReader(tmp_path).read_content(
        UUID(int=1), ref.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert result.content == content


@pytest.mark.parametrize(("outside",), [(False,), (True,)])
@pytest.mark.parametrize(("node",), [("content",), ("prefix",), ("directory",)])
@pytest.mark.parametrize("named_trajectory", [False, True])
async def test_internal_symlinks_are_always_rejected(
    tmp_path: Path, node: str, outside: bool, named_trajectory: bool
) -> None:
    ref = await FileArtifactStore(tmp_path).put(
        ArtifactWrite(
            run_id=UUID(int=1),
            type="run_trajectory" if named_trajectory else "evaluation_stdout",
            name="task_model_2026-10-22.json" if named_trajectory else None,
            media_type="text/plain",
        ),
        b"visible",
    )
    assert ref.sha256 is not None
    target = Path(unquote(urlsplit(ref.uri).path))
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
