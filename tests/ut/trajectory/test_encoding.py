import json

import pytest

from miniagent.trajectory.export import iter_json_bytes


def test_json_encoding_counts_escaping():
    value = {"text": '汉🙂"\n\\', "number": "0.000020"}
    encoded = b"".join(iter_json_bytes(value))
    assert json.loads(encoded) == value
    assert len(encoded) == len(
        json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        {
            "model_limits": {
                "context_window_tokens": 12345,
                "max_output_tokens": 2048,
                "api_key": "forbidden",
            }
        },
        {
            "benchmark": {
                "instance_id": "task",
                "task_digest": "a" * 64,
                "environment": {"TOKEN": "forbidden"},
            }
        },
        {"provider_endpoint_identity": "https://user:forbidden@example.test/v1"},
        {"provider_endpoint_identity": "https://example.test/v1?api_key=forbidden"},
    ],
)
def test_definition_rejects_framework_credentials_in_auxiliary_fields(forbidden):
    from pydantic import ValidationError

    from miniagent.runtime import RuntimeConfig
    from miniagent.trajectory import TrajectoryDefinition
    from tests.support.runtime.runtime import agent

    with pytest.raises(ValidationError):
        TrajectoryDefinition(
            task="task", agent=agent(), budgets=RuntimeConfig(), **forbidden
        )
