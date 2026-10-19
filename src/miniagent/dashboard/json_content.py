from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, NoReturn, cast

from pydantic import JsonValue

from miniagent.dashboard.config import LIMITS

if TYPE_CHECKING:
    from miniagent.dashboard.reads import ReadBudget


class ContentError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise ContentError("json_duplicate_key")
        result[key] = value
    return result


def _nonfinite(value: str) -> NoReturn:
    raise ContentError("json_nonfinite")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ContentError("json_nonfinite")
    return number


def parse_json(content: str | bytes, *, budget: ReadBudget | None = None) -> JsonValue:
    if budget is not None:
        budget.check()
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        stack: list[str] = []
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if budget is not None and index % LIMITS.block_bytes == 0:
                budget.check()
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
                if len(stack) > LIMITS.json_depth:
                    raise ContentError("json_depth")
            elif char in "]}" and (
                not stack or stack.pop() != ("[" if char == "]" else "{")
            ):
                raise ContentError("json_invalid")
        result = cast(
            JsonValue,
            json.loads(
                text,
                object_pairs_hook=_object,
                parse_constant=_nonfinite,
                parse_float=_float,
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContentError("json_invalid") from exc
    if budget is not None:
        budget.check()
    return result
