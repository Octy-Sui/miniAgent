import pytest
from pydantic import ValidationError

from miniagent.cli.settings import Settings


def test_pull_is_disabled_by_default():
    assert (
        Settings(
            _env_file=None,
            openai_api_key="test",
            openai_base_url="https://example.test",
        ).swebench_allow_image_pull
        is False
    )


@pytest.mark.parametrize(
    ("values",),
    [
        ({"candidate_submission_timeout_seconds": 0},),
        ({"swebench_cleanup_timeout_seconds": -1},),
        ({"patch_export_cleanup_timeout_seconds": 60},),
        ({"swebench_process_timeout_seconds": 1800},),
        ({"swebench_test_timeout_seconds": 0},),
        ({"artifact_collection_timeout_seconds": 0},),
        ({"swebench_artifact_timeout_seconds": float("inf")},),
    ],
)
def test_invalid_evaluation_budgets_are_rejected(values):
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            openai_api_key="test",
            openai_base_url="https://example.test",
            **values,
        )
