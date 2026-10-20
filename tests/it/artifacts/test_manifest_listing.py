from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactNotFoundError, ArtifactWrite, FileArtifactStore


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
async def test_tc_u17_01(tmp_path: Path, damage: str) -> None:
    store = FileArtifactStore(tmp_path)
    run_id = uuid4()
    refs = [
        await store.put(ArtifactWrite(run_id=run_id, type="stdout"), str(i).encode())
        for i in range(3)
    ]
    body = Path(refs[0].uri.removeprefix("file://"))
    if damage == "missing":
        body.unlink()
    else:
        body.write_bytes(b"changed")
    first = await store.list_records_for_run(run_id, limit=2)
    assert len(first.records) == 2
    assert first.next_cursor is not None
    second = await store.list_records_for_run(run_id, cursor=first.next_cursor, limit=2)
    assert {r.artifact_id for r in (*first.records, *second.records)} == {
        ref.artifact_id for ref in refs
    }
    assert second.next_cursor is None
    assert [str(r.artifact_id) for r in (*first.records, *second.records)] == sorted(
        str(ref.artifact_id) for ref in refs
    )
    for query in (
        store.read(refs[0]),
        store.get_record(refs[0]),
        store.list_for_run(run_id),
    ):
        with pytest.raises(
            ArtifactNotFoundError, match="Artifact (not found|content changed)"
        ):
            await query


async def test_tc_u17_05(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    run_id = uuid4()
    for i in range(3):
        await store.put(ArtifactWrite(run_id=run_id, type="stdout"), bytes([i]))
    first = await store.list_records_for_run(run_id, limit=1)
    remaining = sorted((tmp_path / "manifests" / run_id.hex).glob("*.json"))[1:]
    remaining[-1].write_text("broken")
    assert await store.list_records_for_run(run_id, limit=1) == first
    with pytest.raises(ArtifactNotFoundError, match="Invalid artifact manifest"):
        await store.list_records_for_run(run_id, cursor=first.next_cursor, limit=2)


async def test_cursor_rejects_wrong_run_and_malformed_values(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    run_id = uuid4()
    for i in range(2):
        await store.put(ArtifactWrite(run_id=run_id, type="stdout"), bytes([i]))
    first = await store.list_records_for_run(run_id, limit=1)
    for cursor in (first.next_cursor, "../escape", "garbage"):
        with pytest.raises(ValueError, match="cursor"):
            await store.list_records_for_run(uuid4(), cursor=cursor)
    with pytest.raises(ValueError, match="positive"):
        await store.list_records_for_run(run_id, limit=0)
    assert (await store.list_records_for_run(uuid4())).records == ()


async def test_manifest_run_must_match_directory(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    run_id = uuid4()
    ref = await store.put(ArtifactWrite(run_id=run_id, type="stdout"), b"body")
    manifest = tmp_path / "manifests" / run_id.hex / f"{ref.artifact_id}.json"
    manifest.write_text(manifest.read_text().replace(str(run_id), str(uuid4())))
    with pytest.raises(ArtifactNotFoundError, match="outside"):
        await store.list_records_for_run(run_id)
