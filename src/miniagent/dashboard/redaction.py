import re
from typing import Literal

from pydantic import JsonValue

REDACTION_VERSION = 1
_REDACTED = "[已脱敏]"
_USAGE_COUNTS = frozenset(
    {
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
        "model_requests",
        "unconfirmed_model_requests",
        "tool_calls",
        "active_duration_ms",
    }
)
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_\-\s])(?:authorization|api[_\-\s]?key|tokens?|secret|password)(?:$|[_\-\s])",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY)-----.*?(?:-----END \1-----|\Z)",
    re.DOTALL,
)
_CREDENTIAL = re.compile(
    r"(?<![\w])(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})(?![\w])"
)
_AUTHORIZATION = re.compile(r"\bauthorization\s*:\s*[^\r\n]+", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[^\s\"'<>]+", re.IGNORECASE)
_ASSIGNMENT = re.compile(
    r"\b(?:[\w-]*[_-])?(?:authorization|api[_-]?key|tokens?|secret|password)"
    r"(?:[_-][\w-]+)?\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    for pattern in (_PRIVATE_KEY, _AUTHORIZATION, _BEARER, _ASSIGNMENT, _CREDENTIAL):
        text = pattern.sub(_REDACTED, text)
    return text


def redact_json(
    value: JsonValue,
    *,
    schema: Literal["run_usage", "event", "summary", "metadata", "unknown"] = "unknown",
) -> JsonValue:
    event_type = value.get("type") if isinstance(value, dict) else None

    def usage_path(path: tuple[str | int, ...]) -> bool:
        if schema == "run_usage":
            return path == ()
        if schema != "event":
            return False
        if event_type == "compaction_attempt_recorded":
            return path == ("result", "usage")
        return (
            event_type in ("compaction_completed", "compaction_failed")
            and len(path) == 3
            and path[0] == "attempts"
            and isinstance(path[1], int)
            and path[2] == "usage"
        )

    def visit(item: JsonValue, path: tuple[str | int, ...]) -> JsonValue:
        if isinstance(item, dict):
            result: dict[str, JsonValue] = {}
            for key, child in item.items():
                allowed = (
                    usage_path(path)
                    and key in _USAGE_COUNTS
                    and type(child) is int
                    and child >= 0
                )
                sensitive = bool(_SENSITIVE_KEY.search(key))
                result[redact_text(key)] = (
                    _REDACTED
                    if sensitive and not allowed
                    else visit(child, (*path, key))
                )
            return result
        if isinstance(item, list):
            return [visit(child, (*path, index)) for index, child in enumerate(item)]
        return redact_text(item) if isinstance(item, str) else item

    return visit(value, ())
