from pathlib import Path
from uuid import UUID

import pytest

from miniagent.dashboard.projections.workspace import choose_patch, parse_diff
from miniagent.dashboard.reads import ManifestItem, PatchDiscovery, ReadBudget
from tests.dashboard.test_manifests import manifest


def budget() -> ReadBudget:
    return ReadBudget(5, monotonic=lambda: 0)


def test_patch_selection_uses_explicit_evidence(tmp_path: Path) -> None:
    records = tuple(
        ManifestItem(UUID(int=n), manifest(tmp_path, n, "workspace_patch"), ())
        for n in (1, 99)
    )
    discovery = PatchDiscovery(records, True, ())
    assert choose_patch(discovery, (UUID(int=1),)).artifact_id == UUID(int=1)
    assert choose_patch(discovery, ()).artifact_id is None
    assert choose_patch(discovery, (UUID(int=1), UUID(int=99))).artifact_id is None
    assert (
        choose_patch(discovery, (UUID(int=1),), association_conflict=True).artifact_id
        is None
    )
    assert choose_patch(PatchDiscovery(records[:1], True, ()), ()).artifact_id == UUID(
        int=1
    )
    assert choose_patch(PatchDiscovery((), True, ()), ()).artifact_id is None
    assert choose_patch(discovery, (UUID(int=200),)).artifact_id is None


def test_partial_scan_never_selects_a_default(tmp_path: Path) -> None:
    item = ManifestItem(UUID(int=1), manifest(tmp_path, 1, "workspace_patch"), ())
    assert (
        choose_patch(
            PatchDiscovery((item,), False, ()), (item.artifact_id,)
        ).artifact_id
        is None
    )


@pytest.mark.parametrize(
    ("text", "old", "new", "kind"),
    [
        (
            "diff --git a/a b/a\nnew file mode 100644\n--- /dev/null\n+++ b/a\n@@ -0,0 +1 @@\n+hello\n",
            None,
            "a",
            "add",
        ),
        (
            "diff --git a/a b/a\ndeleted file mode 100644\n--- a/a\n+++ /dev/null\n@@ -1 +0,0 @@\n-hello\n",
            "a",
            None,
            "delete",
        ),
        (
            "diff --git a/old name b/new name\nsimilarity index 100%\nrename from old name\nrename to new name\n",
            "old name",
            "new name",
            "rename",
        ),
        (
            'diff --git "a/\\344\\270\\255 x" "b/\\344\\270\\255 x"\nBinary files "a/\\344\\270\\255 x" and "b/\\344\\270\\255 x" differ\n',
            "中 x",
            "中 x",
            "binary",
        ),
    ],
)
def test_diff_paths_and_incomplete_parse(
    text: str, old: str | None, new: str | None, kind: str
) -> None:
    result = parse_diff(text, budget=budget())
    assert result.complete
    assert (
        result.files[0].old_path,
        result.files[0].new_path,
        result.files[0].kind,
    ) == (old, new, kind)
    assert result.files[0].text


@pytest.mark.parametrize(
    "text",
    [
        "unexpected secret_token=abc123",
        "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n+new\n",
        "diff --git a/a b/a\nunknown format\n",
    ],
)
def test_unknown_or_truncated_diff_remains_incomplete(text: str) -> None:
    result = parse_diff(text, budget=budget())
    assert not result.complete
    assert result.files
    assert "abc123" not in result.files[0].text
    assert "files_mismatch" not in {issue.code for issue in result.issues}


def test_deadline_preserves_partial_diff() -> None:
    result = parse_diff(
        "diff --git a/a b/a\n", budget=ReadBudget(0, monotonic=lambda: 0)
    )
    assert not result.complete
    assert "files_mismatch" not in {issue.code for issue in result.issues}


def test_diff_compares_original_paths_before_redaction() -> None:
    text = "diff --git a/token=secret-one b/token=secret-one\n--- a/token=secret-one\n+++ b/token=secret-one\n@@ -1 +1 @@\n-old\n+new\n"
    parsed = parse_diff(text, budget=budget(), recorded_files=("token=secret-two",))
    assert parsed.complete
    assert "files_mismatch" in {issue.code for issue in parsed.issues}
    assert "secret-one" not in str(parsed)
    partial = parse_diff(text[:-5], budget=budget(), recorded_files=("other",))
    assert not partial.complete
    assert "files_mismatch" not in {issue.code for issue in partial.issues}
