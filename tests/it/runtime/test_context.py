from uuid import uuid4

import pytest

from tests.support.runtime.context import (
    summary_plan,
    summary_response,
)


@pytest.mark.parametrize(
    ("mutation",),
    [("false_excerpt",), ("false_claim",), ("wrong_run",), ("absent",), ("outside",)],
)
async def test_existing_source_with_false_claim_is_rejected(tmp_path, mutation) -> None:
    from dataclasses import replace

    from miniagent.runtime.context import InvalidSummaryError, validate_summary
    from miniagent.runtime.snapshots import NewRunSnapshot
    from miniagent.storage.sqlite import SQLiteRunStore

    plan = replace(summary_plan(), source_snapshot_version=1)
    store = SQLiteRunStore(tmp_path / "sources.sqlite3")
    await store.initialize()
    await store.create(plan.state)
    await store.save(
        NewRunSnapshot(
            run_id=plan.state.identity.run_id, state=plan.state, event_sequence=0
        ),
        expected_version=0,
    )
    evidence = {
        "claim": "done step 1",
        "excerpt": "done step 1",
        "source": {
            "kind": "snapshot",
            "run_id": str(plan.state.identity.run_id),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 4,
        },
    }
    if mutation == "false_excerpt":
        evidence["excerpt"] = "text absent from the source"
    if mutation == "false_claim":
        evidence["claim"] = "everything succeeded"
    if mutation == "wrong_run":
        evidence["source"]["run_id"] = str(uuid4())
    if mutation == "absent":
        evidence["source"]["snapshot_version"] = 99
    if mutation == "outside":
        evidence["source"]["message_end"] = 12
    with pytest.raises(InvalidSummaryError, match="source"):
        await validate_summary(
            summary_response(verified_evidence=[evidence]), plan, store
        )


async def test_verified_excerpt_from_real_snapshot(tmp_path) -> None:
    from dataclasses import replace

    from miniagent.runtime.context import validate_summary
    from miniagent.runtime.snapshots import NewRunSnapshot
    from miniagent.storage.sqlite import SQLiteRunStore

    plan = replace(summary_plan(), source_snapshot_version=1)
    store = SQLiteRunStore(tmp_path / "sources.sqlite3")
    await store.initialize()
    await store.create(plan.state)
    await store.save(
        NewRunSnapshot(
            run_id=plan.state.identity.run_id, state=plan.state, event_sequence=0
        ),
        expected_version=0,
    )
    evidence = {
        "claim": "done step 1",
        "excerpt": "done step 1",
        "source": {
            "kind": "snapshot",
            "run_id": str(plan.state.identity.run_id),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 4,
        },
    }
    candidate = await validate_summary(
        summary_response(verified_evidence=[evidence]), plan, store
    )
    assert "done step 1" in (candidate.messages[2].content or "")


async def test_mixed_source_proof_cannot_hide_false_structured_value(tmp_path) -> None:
    from dataclasses import replace

    from miniagent.runtime.context import InvalidSummaryError, validate_summary
    from miniagent.runtime.snapshots import NewRunSnapshot
    from miniagent.storage.sqlite import SQLiteRunStore

    plan = replace(summary_plan(), source_snapshot_version=1)
    store = SQLiteRunStore(tmp_path / "mixed-proof.sqlite3")
    await store.initialize()
    await store.create(plan.state)
    await store.save(
        NewRunSnapshot(
            run_id=plan.state.identity.run_id, state=plan.state, event_sequence=0
        ),
        expected_version=0,
    )
    evidence = {
        "claim": "done step 1",
        "excerpt": "done step 1",
        "field_path": ["1", "content"],
        "value": "false structured value",
        "source": {
            "kind": "snapshot",
            "run_id": str(plan.state.identity.run_id),
            "snapshot_version": 1,
            "message_start": 2,
            "message_end": 4,
        },
    }
    with pytest.raises(InvalidSummaryError, match="source"):
        await validate_summary(
            summary_response(verified_evidence=[evidence]), plan, store
        )
