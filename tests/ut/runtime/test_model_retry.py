from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest

from miniagent.models.protocol import ModelFailure
from miniagent.providers.failures import retry_after
from miniagent.runtime.config import ModelRetryConfig, RuntimeConfig
from miniagent.runtime.retry import retry_delay


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("rate_limit", [10, 30, 60, 120, 120]),
        ("server", [1, 2, 5, 10, 10]),
        ("gateway", [5, 10, 20, 40, 40]),
        ("connection", [1, 2, 5, 10, 10]),
        ("read_timeout", [5, 10, 20, 40, 40]),
    ],
)
def test_retry_staircase_saturates_without_reset(category, expected):
    failure = ModelFailure(category=category, retryable=True)
    assert [
        retry_delay(failure, n, ModelRetryConfig(), jitter=0) for n in range(1, 6)
    ] == expected


def test_retry_after_is_a_floor_even_with_jitter():
    failure = ModelFailure(category="server", retryable=True, retry_after_seconds=100)
    assert retry_delay(failure, 1, ModelRetryConfig(), jitter=1) == 100.2


@pytest.mark.parametrize("value", ["bad", "NaN", "inf", "Thu, 01 Jan 1970 00:00:00"])
def test_invalid_retry_after_cannot_create_an_unbounded_wait(value):
    assert retry_after(value) is None


def test_retry_after_accepts_http_dates():
    delay = retry_after(format_datetime(datetime.now(UTC) + timedelta(seconds=60)))
    assert delay is not None and 58 <= delay <= 60


def test_historical_runtime_config_does_not_gain_fields_in_its_frozen_digest():
    original = RuntimeConfig().model_dump(mode="json")
    assert "model_retry" not in original
    assert RuntimeConfig.model_validate(original).model_dump(mode="json") == original
