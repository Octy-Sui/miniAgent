from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

import pytest

from miniagent.artifacts import (
    ArtifactNotFoundError,
    ArtifactWrite,
    FileArtifactStore,
)


async def test_records_survive_reopening_and_preserve_metadata(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    run_id = uuid4()
    store = FileArtifactStore(root)
    ref = await store.put(
        ArtifactWrite(
            run_id=run_id,
            type="workspace_patch",
            name="changes.patch",
            media_type="text/x-diff",
            metadata={"files": ["src/app.py"], "base_revision": "abc123"},
        ),
        b"diff content",
    )

    reopened = FileArtifactStore(root)
    record = await reopened.get_record(ref)

    assert record.artifact_id == ref.artifact_id
    assert record.run_id == run_id
    assert record.metadata == {
        "files": ["src/app.py"],
        "base_revision": "abc123",
    }
    assert await reopened.read(ref) == b"diff content"
    assert await reopened.list_for_run(run_id) == [record]


async def test_same_content_has_independent_records(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    first_run = uuid4()
    second_run = uuid4()
    first = await store.put(
        ArtifactWrite(
            run_id=first_run,
            type="evidence",
            metadata={"source": "first"},
        ),
        b"same",
    )
    second = await store.put(
        ArtifactWrite(
            run_id=second_run,
            type="evidence",
            metadata={"source": "second"},
        ),
        b"same",
    )

    assert first.artifact_id != second.artifact_id
    assert first.uri == second.uri
    assert (await store.list_for_run(first_run))[0].metadata == {"source": "first"}
    assert (await store.list_for_run(second_run))[0].metadata == {"source": "second"}


async def test_named_trajectories_preserve_filenames_without_overwriting(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    name = "修复任务_model_2026-10-22.json"
    first = await store.put(
        ArtifactWrite(run_id=uuid4(), type="run_trajectory", name=name), b'{"run":1}'
    )
    second = await store.put(
        ArtifactWrite(run_id=uuid4(), type="run_trajectory", name=name), b'{"run":2}'
    )
    reopened = FileArtifactStore(store.root)
    assert first.uri != second.uri
    for ref, content in ((first, b'{"run":1}'), (second, b'{"run":2}')):
        path = Path(unquote(urlparse(ref.uri).path))
        assert path.name == name
        assert path.read_bytes() == content
        assert await reopened.read(ref) == content


async def test_changed_content_is_rejected(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    ref = await store.put(
        ArtifactWrite(run_id=run_id, type="evidence"),
        b"original",
    )
    Path(ref.uri.removeprefix("file://")).write_bytes(b"changed")

    with pytest.raises(ArtifactNotFoundError, match="content changed"):
        await store.read(ref)

    with pytest.raises(ArtifactNotFoundError, match="content changed"):
        await store.get_record(ref)

    with pytest.raises(ArtifactNotFoundError, match="content changed"):
        await store.list_for_run(run_id)


async def test_missing_content_is_rejected_by_run_query(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    ref = await store.put(
        ArtifactWrite(run_id=run_id, type="evidence"),
        b"content",
    )
    Path(ref.uri.removeprefix("file://")).unlink()

    with pytest.raises(ArtifactNotFoundError, match="Artifact not found"):
        await store.list_for_run(run_id)


@pytest.mark.parametrize("artifact_type", ["evidence", "run_trajectory"])
async def test_artifact_path_fields_must_be_safe_segments(
    tmp_path: Path, artifact_type: str
) -> None:
    store = FileArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="type"):
        await store.put(
            ArtifactWrite(run_id=uuid4(), type="../escape"),
            b"content",
        )
    with pytest.raises(ValueError, match="name"):
        await store.put(
            ArtifactWrite(run_id=uuid4(), type=artifact_type, name="../escape"),
            b"content",
        )


async def test_multi_log_collection_rejects_any_corrupted_content(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    refs = [
        await store.put(
            ArtifactWrite(run_id=run_id, type="evaluation_stdout"),
            bytes([number]) * (1024 * 1024),
        )
        for number in range(4)
    ]
    assert {record.reference() for record in await store.list_for_run(run_id)} == set(
        refs
    )
    Path(urlparse(refs[2].uri).path).write_bytes(b"corrupt")
    with pytest.raises(ArtifactNotFoundError, match="content changed"):
        await store.list_for_run(run_id)
