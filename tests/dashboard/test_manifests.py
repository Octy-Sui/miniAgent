import hashlib
import os
from pathlib import Path
from uuid import UUID

from miniagent.artifacts.contracts import ArtifactRecord
from miniagent.dashboard.artifacts import ArtifactReader
from miniagent.dashboard.reads import ReadBudget


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


def test_manifest_page_does_not_validate_all_contents(tmp_path: Path) -> None:
    for number in range(1, 102):
        manifest(tmp_path, number)
    opened = []

    def open_fd(path, flags, mode=0o777, *, dir_fd=None):
        opened.append(str(path))
        return os.open(path, flags, mode, dir_fd=dir_fd)

    reader = ArtifactReader(tmp_path, open_fd=open_fd)
    first = reader.list_manifests(
        UUID(int=1), after_id=None, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert len(first.items) == 100
    assert first.complete
    second = reader.list_manifests(
        UUID(int=1), after_id=first.next_id, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert [item.artifact_id for item in second.items] == [UUID(int=101)]
    assert second.next_id is None
    assert not any(path == "content" or len(path) == 64 for path in opened)
    assert (
        reader.read_manifest(
            UUID(int=1), UUID(int=101), budget=ReadBudget(5, monotonic=lambda: 0)
        ).record
        is not None
    )


def test_corrupt_manifest_does_not_hide_other_items(tmp_path: Path) -> None:
    manifest(tmp_path, 1)
    manifest(tmp_path, 2)
    (tmp_path / "manifests" / UUID(int=1).hex / f"{UUID(int=1)}.json").write_text(
        "{broken"
    )
    page = ArtifactReader(tmp_path).list_manifests(
        UUID(int=1), after_id=None, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert page.items[0].artifact_id == UUID(int=1)
    assert page.items[0].record is None
    assert page.items[0].issues
    assert page.items[1].record is not None


def test_partial_discovery_cannot_prove_unique_patch(tmp_path: Path) -> None:
    manifest(tmp_path, 1, "workspace_patch")
    manifest(tmp_path, 2, "workspace_patch")
    now = [0.0]
    opened = []

    def open_fd(path, flags, mode=0o777, *, dir_fd=None):
        fd = os.open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith(".json"):
            opened.append(str(path))
            if len(opened) == 2:
                now[0] = 6
        return fd

    discovery = ArtifactReader(tmp_path, open_fd=open_fd).discover_patches(
        UUID(int=1), budget=ReadBudget(5, monotonic=lambda: now[0])
    )
    assert not discovery.complete
    assert len(discovery.items) <= 1
    assert discovery.issues


def test_junk_directory_entry_is_an_issue_without_inventing_an_artifact(
    tmp_path: Path,
) -> None:
    manifest(tmp_path, 1)
    (
        tmp_path / "manifests" / UUID(int=1).hex / "untrusted-name-secret.json"
    ).write_text("{}")
    page = ArtifactReader(tmp_path).list_manifests(
        UUID(int=1), after_id=None, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert [item.artifact_id for item in page.items] == [UUID(int=1)]
    assert "unexpected_manifest_entry" in {issue.code for issue in page.issues}


import pytest

from miniagent.dashboard.reads import ReadFailure


@pytest.mark.parametrize(("delta",), [(-2,), (-1,), (0,), (1,), (2,)])
def test_manifest_byte_gate(tmp_path: Path, delta: int) -> None:
    record = manifest(tmp_path, 1)
    record = record.model_copy(update={"metadata": {"padding": ""}})
    overhead = len(record.model_dump_json().encode())
    record = record.model_copy(
        update={"metadata": {"padding": "x" * (1048576 + delta - overhead)}}
    )
    path = tmp_path / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
    path.write_text(record.model_dump_json())
    item = ArtifactReader(tmp_path).read_manifest(
        record.run_id, record.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert (item.record is not None) == (delta <= 0)
    if delta > 0:
        assert "read_limit_exceeded" in {issue.code for issue in item.issues}


@pytest.mark.parametrize(("node",), [("manifest",), ("directory",)])
def test_manifest_symlinks_are_rejected_even_inside_root(
    tmp_path: Path, node: str
) -> None:
    record = manifest(tmp_path, 1)
    target = (
        tmp_path / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
        if node == "manifest"
        else tmp_path / "manifests"
    )
    replacement = tmp_path / "original-node"
    target.rename(replacement)
    target.symlink_to(replacement, target_is_directory=node == "directory")
    with pytest.raises(ReadFailure) as error:
        ArtifactReader(tmp_path).read_manifest(
            record.run_id, record.artifact_id, budget=ReadBudget(5, monotonic=lambda: 0)
        )
    assert error.value.status_code == 409


def test_missing_manifest_directory_is_an_empty_current_discovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "not-created"
    reader = ArtifactReader(root)
    assert (
        reader.list_manifests(
            UUID(int=1), after_id=None, budget=ReadBudget(5, monotonic=lambda: 0)
        ).items
        == ()
    )
    assert reader.discover_patches(
        UUID(int=1), budget=ReadBudget(5, monotonic=lambda: 0)
    ).complete
    assert not root.exists()
