from collections.abc import Iterator
from typing import Any

import orjson

from miniagent.tools.contracts import ToolOutputLimits, ToolResult


def render_tool_result(result: ToolResult, limits: ToolOutputLimits) -> str:
    payload = result.tool_result_metadata()
    output = payload["output"]
    assert isinstance(output, dict)
    fields = list(_text_fields(payload["data"]))
    for container, key in fields:
        value = container[key]
        shortened = _utf8_prefix(value, limits.max_content_bytes)
        if shortened != value:
            container[key] = shortened
            _mark_truncated(output, key)

    for field in ("metrics", "workspace_delta", "artifacts"):
        if _encoded_size(payload) <= limits.max_message_bytes:
            break
        if payload.get(field):
            del payload[field]
            _record_omission(output, field)

    if _encoded_size(payload) > limits.max_message_bytes:
        _fit_payload(payload, fields, limits.max_message_bytes)

    encoded = orjson.dumps(payload)
    if len(encoded) > limits.max_message_bytes:
        raise ValueError("Tool result metadata exceeds the message limit")
    return encoded.decode("utf-8")


def _fit_payload(
    payload: dict[str, Any], fields: list[tuple[Any, str | int]], max_bytes: int
) -> None:
    output = payload["output"]
    error = payload.get("error")
    original_omissions = set(output["omitted_fields"])
    if isinstance(error, dict):
        if error.get("issues"):
            error["issues"] = []
            _record_omission(output, "error.issues")
        fields = [(error, "message"), *fields]
        _record_omission(output, "error.message")

    values = [(container, key, container[key]) for container, key in fields]
    for container, key, _ in values:
        container[key] = ""
    baseline = _encoded_size(payload)
    if baseline > max_bytes:
        del payload["data"]
        _record_omission(output, "data")
        output["content_truncated"] = True
        values = [item for item in values if item[0] is error]
        baseline = _encoded_size(payload)
    if baseline > max_bytes:
        raise ValueError("Tool result metadata exceeds the message limit")

    remaining = max_bytes - baseline
    values.sort(key=lambda item: (item[0] is not error, len(item[2])))
    for container, key, value in values:
        fitted = _encoded_prefix(value, remaining)
        container[key] = fitted
        remaining -= len(orjson.dumps(fitted)) - 2
        if fitted != value:
            if container is not error:
                _mark_truncated(output, key)
        elif container is error and "error.message" not in original_omissions:
            output["omitted_fields"].remove("error.message")


def _text_fields(value: Any) -> Iterator[tuple[Any, str | int]]:
    if isinstance(value, dict):
        children = value.items()
    elif isinstance(value, list):
        children = enumerate(value)
    else:
        return
    for key, child in children:
        if isinstance(child, str):
            yield value, key
        else:
            yield from _text_fields(child)


def _mark_truncated(output: dict[str, Any], key: str | int) -> None:
    output["content_truncated"] = True
    if key in ("stdout", "stderr"):
        output[f"{key}_truncated"] = True


def _record_omission(output: dict[str, Any], field: str) -> None:
    if field not in output["omitted_fields"]:
        output["omitted_fields"].append(field)


def _utf8_prefix(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _encoded_prefix(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    if len(orjson.dumps(value)) - 2 <= max_bytes:
        return value
    low, high = 0, min(len(value), max_bytes)
    while low < high:
        middle = (low + high + 1) // 2
        if len(orjson.dumps(value[:middle])) - 2 <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(orjson.dumps(payload))
