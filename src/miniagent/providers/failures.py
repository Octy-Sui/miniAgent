from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from ssl import SSLCertVerificationError

from openai import APIConnectionError, APIStatusError

from miniagent.models.protocol import ModelFailure


def retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            delay = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0, delay) if isfinite(delay) else None


def http_failure(error: APIStatusError) -> ModelFailure:
    permanent_codes = {
        "BudgetLimitExceeded",
        "CommodityNotPurchased",
        "PrepaidBillOverdue",
        "PostpaidBillOverdue",
        "Arrearage",
        "InvalidApiKey",
        "AccessDenied",
    }
    known_codes = permanent_codes | {
        "Throttling.AllocationQuota",
        "insufficient_quota",
        "Throttling.RateQuota",
        "Throttling.Concurrency",
        "Throttling.BurstRate",
        "Throttling.ServiceOverloaded",
        "Throttling.ResourceExhausted",
        "InternalError",
        "GatewayTimeout",
        "ModelServiceFailed",
        "ServiceUnavailable",
        "ModelUnavailable",
    }
    code = str(error.code or "")
    if (
        code in permanent_codes
        or "free allocated quota exceeded" in error.message.lower()
    ):
        category = "permanent"
    elif error.status_code == 429:
        category = "rate_limit"
    elif error.status_code in (408, 502, 503, 504):
        category = "gateway"
    elif error.status_code == 500:
        category = "server"
    else:
        category = "permanent"
    return ModelFailure(
        category=category,
        retryable=category != "permanent",
        status_code=error.status_code,
        provider_code=code if code in known_codes else None,
        retry_after_seconds=retry_after(error.response.headers.get("retry-after")),
    )


def connection_failure(error: APIConnectionError) -> ModelFailure:
    cause = error.__cause__
    kind = type(cause).__name__
    category = "read_timeout" if kind == "ReadTimeout" else "connection"
    current = cause
    for _ in range(8):
        if current is None:
            break
        if isinstance(current, SSLCertVerificationError):
            category = "permanent"
            break
        current = current.__cause__ or current.__context__
    return ModelFailure(
        category=category,
        retryable=category != "permanent",
        exception_type=kind
        if kind
        in {
            "ConnectTimeout",
            "ReadTimeout",
            "WriteTimeout",
            "PoolTimeout",
            "ConnectError",
            "ReadError",
            "WriteError",
            "RemoteProtocolError",
        }
        else type(error).__name__,
    )
