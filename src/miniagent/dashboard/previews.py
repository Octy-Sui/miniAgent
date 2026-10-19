import json
import re
from collections.abc import Iterable, Iterator
from itertools import chain
from pathlib import Path

from pydantic import JsonValue

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.reads import (
    ReadBudget,
    ReadFailure,
    ReadIssue,
    VerifiedContent,
)
from miniagent.dashboard.redaction import redact_json, redact_text
from miniagent.dashboard.views import FieldView, PreviewView

_KINDS = frozenset(
    (
        "workspace_patch",
        "evaluation_config",
        "evaluation_summary",
        "evaluation_report",
        "evaluation_predictions",
        "evaluation_stdout",
        "evaluation_stderr",
        "evaluation_test_output",
        "evaluation_instance_log",
    )
)
_NEWLINES = frozenset(
    ("\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
)
_SURROGATE = re.compile(r"[\ud800-\udfff]")


def _metadata(content: VerifiedContent) -> tuple[FieldView, ...]:
    record = content.record
    name = Path(redact_text(record.name)).name if record.name else "未记录"
    values = (
        ("artifact_id", "Artifact ID", str(record.artifact_id)),
        ("type", "类型", record.type),
        ("name", "名称", name),
        ("size_bytes", "登记字节数", str(record.size_bytes)),
        ("sha256", "SHA-256", record.sha256),
        ("media_type", "内容类型", record.media_type),
        (
            "content_validation",
            "内容摘要校验",
            "已校验" if content.content is not None else "未校验",
        ),
    )
    fields = []
    for key, label, value in values:
        text = redact_text(value)
        try:
            if len(text.encode()) > LIMITS.scalar_bytes:
                text = "超出展示上限"
        except UnicodeError:
            text = "不可显示的文本"
        fields.append(
            FieldView(
                key,
                label,
                text,
                "Artifact manifest"
                if key != "content_validation"
                else "Artifact reader",
            )
        )
    return tuple(fields)


def _valid_unicode(value: JsonValue, budget: ReadBudget) -> bool:
    count = 0

    def valid(item: JsonValue) -> bool:
        nonlocal count
        count += 1
        if count % 1024 == 0:
            budget.check()
        if isinstance(item, str):
            return _SURROGATE.search(item) is None
        if isinstance(item, list):
            return all(valid(child) for child in item)
        if isinstance(item, dict):
            return all(
                _SURROGATE.search(key) is None and valid(child)
                for key, child in item.items()
            )
        return True

    result = valid(value)
    budget.check()
    return result


def _tokens(chunks: Iterable[str]) -> Iterator[str]:
    characters = iter(chain.from_iterable(chunks))
    pending = next(characters, None)
    while pending is not None:
        char = pending
        following = next(characters, None)
        if char == "\r" and following == "\n":
            yield "\r\n"
            pending = next(characters, None)
        else:
            yield char
            pending = following


def _window(
    chunks: Iterable[str], offset: int, budget: ReadBudget
) -> tuple[str, int | None, bool]:
    tokens = iter(_tokens(chunks))
    output = bytearray()
    position = 0
    checked = 0
    line_count = 0
    previous_newline = True
    starts_inline = False
    started = False
    for token in tokens:
        encoded = token.encode("utf-8")
        checked += len(encoded)
        if checked >= LIMITS.block_bytes:
            budget.check()
            checked = 0
        if position + len(encoded) <= offset:
            position += len(encoded)
            previous_newline = token in _NEWLINES
            continue
        if position < offset:
            prefix = encoded[: offset - position]
            try:
                token = encoded[offset - position :].decode("utf-8")
                previous_newline = prefix.decode("utf-8") in _NEWLINES
            except UnicodeError as exc:
                raise ReadFailure(422, "preview_offset_invalid") from exc
            encoded = token.encode("utf-8")
            position = offset
        if not started:
            starts_inline = offset > 0 and not previous_newline
            started = True
        if len(output) + len(encoded) > LIMITS.preview_bytes:
            return (
                output.decode(),
                offset + len(output),
                starts_inline or not previous_newline,
            )
        output.extend(encoded)
        position += len(encoded)
        previous_newline = token in _NEWLINES
        if previous_newline:
            line_count += 1
        if len(output) == LIMITS.preview_bytes or line_count == LIMITS.preview_lines:
            more = next(tokens, None) is not None
            budget.check()
            return (
                output.decode(),
                offset + len(output) if more else None,
                starts_inline or (more and not previous_newline),
            )
    if position < offset:
        raise ReadFailure(422, "preview_offset_invalid")
    budget.check()
    return output.decode(), None, starts_inline if output else False


def project_preview(
    content: VerifiedContent, *, offset: int = 0, budget: ReadBudget
) -> PreviewView:
    budget.check()
    if offset < 0:
        raise ReadFailure(422, "preview_offset_invalid")
    fields = _metadata(content)
    issues = list(content.issues)

    def unavailable(code: str, message: str) -> PreviewView:
        return PreviewView(
            content.record.artifact_id,
            fields,
            None,
            offset,
            None,
            False,
            content.read_at,
            (*issues, ReadIssue(code, message)),
        )

    if content.content is None:
        return unavailable("not_loaded", "内容尚未完成校验")
    media = content.record.media_type.partition(";")[0].strip().lower()
    if content.record.type not in _KINDS or not (
        media.startswith("text/") or media == "application/json"
    ):
        return unavailable("preview_unsupported", "此内容类型只展示登记信息")
    try:
        text = content.content.decode("utf-8")
    except UnicodeError:
        return unavailable("preview_binary", "内容不是有效 UTF-8 文本")
    for index, char in enumerate(text):
        if index % LIMITS.block_bytes == 0:
            budget.check()
        point = ord(char)
        if (point < 32 and char not in "\t\r\n") or 127 <= point <= 159:
            return unavailable("preview_binary", "内容含有不可预览的控制字符")
    if media == "application/json":
        try:
            value = parse_json(text, budget=budget)
        except ContentError as exc:
            return unavailable(exc.code, "JSON 未通过安全解析，仅展示登记信息")
        del text
        redacted = redact_json(value)
        del value
        if not _valid_unicode(redacted, budget):
            return unavailable("preview_unicode", "JSON 含有不可显示的 Unicode 文本")
        chunks = json.JSONEncoder(
            indent=2, ensure_ascii=False, allow_nan=False
        ).iterencode(redacted)
    else:
        text = redact_text(text)
        budget.check()
        chunks = iter((text,))
    shown, next_offset, inline = _window(chunks, offset, budget)
    budget.check()
    return PreviewView(
        content.record.artifact_id,
        fields,
        shown,
        offset,
        next_offset,
        inline,
        content.read_at,
        tuple(issues),
    )
