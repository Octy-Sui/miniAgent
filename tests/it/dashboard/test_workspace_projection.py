from pathlib import Path
from uuid import UUID

from miniagent.dashboard.projections.workspace import choose_patch
from miniagent.dashboard.reads import ManifestItem, PatchDiscovery
from tests.support.dashboard.manifests import manifest


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
