import pytest

from tests.support.context_compaction.cases import manifest
from tests.support.context_compaction.protocol import (
    AcceptanceManifest,
    cost_acceptance_result,
    freeze_manifest,
)


@pytest.mark.parametrize(
    ("savings", "accepted"), [(19.9, False), (20.0, True), (20.1, True)]
)
def test_cost_threshold(savings, accepted) -> None:
    assert (
        cost_acceptance_result(
            baseline=1000,
            compacted=1000 * (1 - savings / 100),
            baseline_successes=3,
            compacted_successes=3,
        )
        is accepted
    )


def test_lower_success_count_or_unknown_usage_not_accepted() -> None:
    assert not cost_acceptance_result(
        baseline=1000, compacted=100, baseline_successes=3, compacted_successes=2
    )
    assert not cost_acceptance_result(
        baseline=None, compacted=100, baseline_successes=3, compacted_successes=3
    )


def test_same_implementer_cannot_sign_acceptance(tmp_path) -> None:
    with pytest.raises(ValueError, match="independent"):
        freeze_manifest(manifest("developer"), tmp_path)


def test_missing_planned_run_oracle_is_rejected() -> None:
    frozen = manifest("independent").model_dump()
    frozen["planned_runs"][0]["task_id"] = "absent"
    with pytest.raises(ValueError):
        AcceptanceManifest.model_validate(frozen)
