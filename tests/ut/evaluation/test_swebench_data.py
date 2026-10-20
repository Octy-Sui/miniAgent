import pytest
from pydantic import ValidationError

from miniagent.evaluation.swebench_data import (
    SWEbenchTaskContext,
)
from tests.support.evaluation.swebench_data import INSTANCE


@pytest.mark.parametrize(
    ("value",),
    [
        ({},),
        ({"instance_id": ""},),
        ({"instance_id": INSTANCE, "unexpected": 1},),
        ({"instance_id": INSTANCE, "evaluation_image": ""},),
    ],
)
def test_invalid_context_is_rejected(value):
    with pytest.raises(ValidationError):
        SWEbenchTaskContext.model_validate(value)
