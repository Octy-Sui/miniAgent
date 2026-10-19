import shutil
from uuid import uuid4

import pytest

from miniagent.environments import LocalOptions, LocalWorkspaceSpec
from miniagent.environments.attachment import WorkspaceAttachmentError
from tests.environments import test_local
from tests.environments.test_local import local_backend, run_git

source_repository = test_local.source_repository


async def retained(tmp_path, source_repository):
    backend = local_backend(tmp_path)
    spec = LocalWorkspaceSpec(
        run_id=uuid4(), options=LocalOptions(source_dir=source_repository)
    )
    workspace = await backend.create(spec)
    return backend, spec, workspace


async def test_attach_keeps_workspace_bytes_index_and_baseline(
    tmp_path, source_repository
):
    """TC-U12-01/02: attach preserves all edits, index bytes and the original baseline."""
    backend, spec, original = await retained(tmp_path, source_repository)
    (original.root / "tracked.txt").write_text("new committed content\n")
    run_git(original.root, "add", "tracked.txt")
    run_git(
        original.root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "new head",
    )
    (original.root / "tracked.txt").write_text("staged edit\n")
    run_git(original.root, "add", "tracked.txt")
    (original.root / "tracked.txt").write_text("unstaged edit\n")
    (original.root / "untracked.txt").write_text("untracked\n")
    index_before = (original.root / ".git/index").read_bytes()
    files_before = {
        path.name: path.read_bytes() for path in original.root.glob("*.txt")
    }
    attachment = original.describe_attachment()
    facts = await backend.inspect_attachment(attachment)
    assert facts.exists and facts.identity_matches and not facts.errors
    attached = await backend.attach(spec, attachment)
    assert attached.workspace_id == original.workspace_id
    assert attached.run_id == original.run_id
    assert attached.root == original.root
    assert (original.root / ".git/index").read_bytes() == index_before
    assert {
        path.name: path.read_bytes() for path in original.root.glob("*.txt")
    } == files_before
    assert (await attached.export_patch()).base_revision == original.base_revision


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("directory", "WORKSPACE_MISSING"),
        ("marker", "WORKSPACE_MISSING"),
        ("run", "WORKSPACE_IDENTITY_MISMATCH"),
        ("workspace", "WORKSPACE_IDENTITY_MISMATCH"),
        ("marker_contents", "WORKSPACE_IDENTITY_MISMATCH"),
        ("outside", "WORKSPACE_IDENTITY_MISMATCH"),
        ("symlink", "WORKSPACE_IDENTITY_MISMATCH"),
        ("ancestor_symlink", "WORKSPACE_IDENTITY_MISMATCH"),
        ("baseline", "WORKSPACE_IDENTITY_MISMATCH"),
    ],
)
async def test_attach_rejects_changed_local_identity(
    tmp_path, source_repository, mutation, code
):
    """TC-U12-03..08: reject each missing or redirected identity independently."""
    backend, spec, original = await retained(tmp_path, source_repository)
    attachment = original.describe_attachment()
    marker = original.root.parent / ".miniagent-workspace"
    if mutation == "directory":
        shutil.rmtree(original.root)
    elif mutation == "marker":
        marker.unlink()
    elif mutation == "run":
        attachment = attachment.model_copy(update={"run_id": uuid4()})
    elif mutation == "workspace":
        attachment = attachment.model_copy(update={"workspace_id": "other"})
    elif mutation == "marker_contents":
        marker.write_text(f"{uuid4()}\nwrong\n")
    elif mutation == "outside":
        attachment = attachment.model_copy(
            update={"repository_root": tmp_path / "outside"}
        )
    elif mutation == "symlink":
        real = original.root.with_name("original-repo")
        original.root.rename(real)
        original.root.symlink_to(real, target_is_directory=True)
    elif mutation == "ancestor_symlink":
        directory = original.root.parent
        real = directory.with_name("original-directory")
        directory.rename(real)
        directory.symlink_to(real, target_is_directory=True)
    else:
        attachment = attachment.model_copy(update={"base_revision": "0" * 40})
    facts = await backend.inspect_attachment(attachment)
    assert not facts.identity_matches
    assert facts.errors[0].code == code
    with pytest.raises(WorkspaceAttachmentError, match=code):
        await backend.attach(spec, attachment)
    if mutation == "directory":
        assert not original.root.exists()
    if mutation == "marker":
        assert not marker.exists()


async def test_attach_rechecks_after_inspection(tmp_path, source_repository):
    backend, spec, original = await retained(tmp_path, source_repository)
    attachment = original.describe_attachment()
    assert (await backend.inspect_attachment(attachment)).identity_matches
    (original.root.parent / ".miniagent-workspace").unlink()
    with pytest.raises(WorkspaceAttachmentError, match="WORKSPACE_MISSING"):
        await backend.attach(spec, attachment)
