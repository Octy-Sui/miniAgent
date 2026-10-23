from datetime import datetime, timedelta

from miniagent.models.protocol import ModelFailure
from miniagent.runtime.config import ModelRetryConfig


def retry_delay(
    failure: ModelFailure, failures: int, config: ModelRetryConfig, *, jitter: float
) -> float:
    if failure.category == "rate_limit":
        delays = config.rate_limit_delays
    elif failure.category == "server":
        delays = config.server_delays
    elif failure.category in ("gateway", "read_timeout"):
        delays = config.gateway_delays
    else:
        delays = config.connection_delays
    base = delays[min(max(0, failures - 1), len(delays) - 1)]
    return (
        max(base, failure.retry_after_seconds or 0)
        + base * config.jitter_ratio * jitter
    )


def next_retry_at(
    failure: ModelFailure,
    failures: int,
    config: ModelRetryConfig,
    *,
    now: datetime,
    deadline: datetime,
    jitter: float,
) -> datetime:
    delay = retry_delay(failure, failures, config, jitter=jitter)
    if delay >= (deadline - now).total_seconds():
        return deadline
    return now + timedelta(seconds=delay)
