from dataclasses import replace
from uuid import UUID

import pytest

from miniagent.dashboard.projections.compaction import project_memory
from miniagent.dashboard.reads import ReadIssue
from miniagent.runtime.context import SnapshotSource, VerifiedEvidence
from tests.support.dashboard.compaction_sources import proof_example


@pytest.mark.parametrize(
    ("claim", "excerpt", "expected"),
    [
        ("first evidence", "first evidence", "verified"),
        ("invented conclusion", "first evidence", "inconsistent"),
        ("content", "content", "inconsistent"),
        ("absent leaf", "absent leaf", "inconsistent"),
    ],
)
def test_existing_source_must_support_claim(
    claim: str, excerpt: str, expected: str
) -> None:
    proof = VerifiedEvidence(
        claim=claim,
        excerpt=excerpt,
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    memory = project_memory(run, boundary, facts)
    assert memory.evidence[0].state == expected
    assert all(field.source_label == "摘要记录" for field in memory.fields)


def test_unloaded_evidence_is_not_missing() -> None:
    proof = VerifiedEvidence(
        claim="first evidence",
        excerpt="first evidence",
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    snapshots = dict(facts.snapshots)
    snapshots[1] = replace(
        snapshots[1], snapshot=None, issues=(ReadIssue("read_limit_exceeded", "超限"),)
    )
    memory = project_memory(
        run, boundary, replace(facts, snapshots=snapshots, unloaded_versions=(1,))
    )
    assert memory.evidence[0].state == "not_loaded"
    assert "source_missing" not in {issue.code for issue in memory.evidence[0].issues}


@pytest.mark.parametrize(
    ("change",),
    [
        ({"run_id": UUID(int=99)},),
        ({"snapshot_version": 2},),
        ({"message_start": 3, "message_end": 4},),
    ],
)
def test_source_identity_and_ancestor_range_are_required(change: dict) -> None:
    source = SnapshotSource.model_validate(
        {
            "kind": "snapshot",
            "run_id": UUID(int=1),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 3,
        }
        | change
    )
    run, boundary, facts = proof_example(
        VerifiedEvidence(
            claim="first evidence", excerpt="first evidence", source=source
        )
    )
    assert project_memory(run, boundary, facts).evidence[0].state == "inconsistent"


@pytest.mark.parametrize(
    ("value", "claim", "expected"),
    [
        ("first evidence", '"first evidence"', "verified"),
        (True, "true", "inconsistent"),
        (1, "1", "inconsistent"),
        ("first evidence", "first evidence", "inconsistent"),
    ],
)
def test_field_proof_checks_actual_type_and_canonical_claim(
    value, claim: str, expected: str
) -> None:
    proof = VerifiedEvidence(
        claim=claim,
        field_path=("0", "content"),
        value=value,
        source=SnapshotSource(
            kind="snapshot",
            run_id=UUID(int=1),
            snapshot_version=1,
            message_start=2,
            message_end=3,
        ),
    )
    run, boundary, facts = proof_example(proof)
    assert project_memory(run, boundary, facts).evidence[0].state == expected
