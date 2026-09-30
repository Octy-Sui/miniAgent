# File   : tool_output.py
# Created: 2026-09-17 15:36:23
# Author : octy Sui

from collections.abc import MutableMapping
from typing import Any

import orjson

from miniagent.tools.contracts import ToolOutputLimits, ToolResult


def render_tool_result(result: ToolResult, limits: ToolOutputLimits) -> str:
    output = result.output.model_copy(deep=True)
    content = _utf8_prefix(result.content, limits.max_content_bytes)
    output.content_truncated = content != result.content
    output.omitted_fields = []

    payload: dict[str, Any] = {
        "tool_call_id": result.tool_call_id,
        "status": result.status.value,
        "content": content,
        "output": output.model_dump(mode="json"),
    }
    if result.duration_ms is not None:
        payload["duration_ms"] = result.duration_ms
    if result.data is not None:
        payload["data"] = result.data
    if result.error is not None:
        payload["error"] = result.error.model_dump(mode="json")
    if result.artifacts:
        payload["artifacts"] = [
            artifact.model_dump(mode="json", exclude_none=True)
            for artifact in result.artifacts
        ]
    if result.metrics:
        payload["metrics"] = result.metrics
    if result.workspace_delta is not None:
        payload["workspace_delta"] = result.workspace_delta.model_dump(mode="json")

    for field in ("data", "metrics", "workspace_delta", "artifacts"):
        if _encoded_size(payload) <= limits.max_message_bytes:
            break
        if field in payload:
            del payload[field]
            _record_omission(payload, field)

    if (
        _encoded_size(payload) > limits.max_message_bytes
        and isinstance(payload.get("error"), MutableMapping)
        and payload["error"].get("issues")
    ):
        del payload["error"]["issues"]
        _record_omission(payload, "error.issues")

    if _encoded_size(payload) > limits.max_message_bytes:
        original_content = payload["content"]
        payload["content"] = ""
        payload["output"]["content_truncated"] = True

        error = payload.get("error")
        if _encoded_size(payload) > limits.max_message_bytes and isinstance(
            error, MutableMapping
        ):
            original_message = error["message"]
            error["message"] = ""
            _record_omission(payload, "error.message")
            if _encoded_size(payload) > limits.max_message_bytes:
                raise ValueError("Tool result metadata exceeds the message limit")
            error["message"] = _largest_fitting_prefix(
                payload,
                error,
                "message",
                original_message,
                limits.max_message_bytes,
            )

        payload["content"] = _largest_fitting_prefix(
            payload,
            payload,
            "content",
            original_content,
            limits.max_message_bytes,
        )

    encoded = orjson.dumps(payload)
    if len(encoded) > limits.max_message_bytes:
        raise ValueError("Tool result exceeds the message limit")
    return encoded.decode("utf-8")


def _utf8_prefix(value: str, max_bytes: int) -> str:
    if len(value.encode("utf-8")) <= max_bytes:
        return value
    low = 0
    high = len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(value[:middle].encode("utf-8")) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _largest_fitting_prefix(
    payload: dict[str, Any],
    container: MutableMapping[str, Any],
    field: str,
    value: str,
    max_bytes: int,
) -> str:
    low = 0
    high = len(value)
    while low < high:
        middle = (low + high + 1) // 2
        container[field] = value[:middle]
        if _encoded_size(payload) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    container[field] = value[:low]
    return value[:low]


def _record_omission(payload: dict[str, Any], field: str) -> None:
    payload["output"]["omitted_fields"].append(field)


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(orjson.dumps(payload))
