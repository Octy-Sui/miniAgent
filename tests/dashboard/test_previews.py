import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest

from miniagent.artifacts.contracts import ArtifactRecord
from miniagent.dashboard.previews import project_preview
from miniagent.dashboard.reads import ReadBudget, ReadFailure, VerifiedContent


def verified(
    content: str | bytes,
    *,
    media_type: str = "text/plain",
    kind: str = "evaluation_stdout",
) -> VerifiedContent:
    data = content.encode() if isinstance(content, str) else content
    return VerifiedContent(
        ArtifactRecord(
            artifact_id=UUID(int=2),
            run_id=UUID(int=1),
            type=kind,
            media_type=media_type,
            uri="file:///private/must-not-display",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ),
        data,
        datetime(2026, 10, 19, tzinfo=UTC),
        (),
    )


def chunks(content: VerifiedContent):
    offset = 0
    result = []
    offsets = []
    while True:
        preview = project_preview(
            content, offset=offset, budget=ReadBudget(5, monotonic=lambda: 0)
        )
        assert preview.text is not None
        result.append(preview.text)
        offsets.append(offset)
        assert len(preview.text.encode()) <= 262144
        assert len(preview.text.splitlines()) <= 2000
        if preview.next_offset is None:
            break
        assert preview.next_offset > offset
        offset = preview.next_offset
    return result, offsets


def test_redacted_preview_chunks_reassemble_exactly() -> None:
    prefix = "a" * 262130 + "\n"
    source = (
        prefix
        + "-----BEGIN PRIVATE KEY-----\n"
        + "private material\n" * 200
        + "-----END PRIVATE KEY-----\nend"
    )
    result, offsets = chunks(verified(source))
    assert "".join(result) == prefix + "[已脱敏]\nend"
    assert offsets == [0, 262144]


def test_full_redaction_precedes_line_pagination() -> None:
    prefix = "visible\n" * 1999
    source = (
        prefix
        + "-----BEGIN RSA PRIVATE KEY-----\nprivate\nmaterial\n-----END RSA PRIVATE KEY-----\ntail"
    )
    result, offsets = chunks(verified(source))
    assert result == [prefix + "[已脱敏]\n", "tail"]
    assert offsets == [0, 16004]


@pytest.mark.parametrize(
    ("source",),
    [
        ("{bad",),
        ('{"token":"one","token":"two"}',),
        ("NaN",),
        ("[" * 65 + "0" + "]" * 65,),
        ('{"message":"\\ud800"}',),
    ],
)
def test_invalid_json_never_falls_back_to_raw_text(source: str) -> None:
    preview = project_preview(
        verified(source, media_type="application/json", kind="evaluation_report"),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert preview.text is None
    assert preview.issues


@pytest.mark.parametrize(
    ("length",), [(262142,), (262143,), (262144,), (262145,), (262146,)]
)
def test_preview_respects_byte_limit(length: int) -> None:
    result, _ = chunks(verified("x" * length))
    assert "".join(result) == "x" * length
    assert len(result) == (1 if length <= 262144 else 2)


@pytest.mark.parametrize(("lines",), [(1998,), (1999,), (2000,), (2001,), (2002,)])
def test_preview_respects_both_limits(lines: int) -> None:
    result, _ = chunks(verified("x\n" * lines))
    assert "".join(result) == "x\n" * lines
    assert len(result) == (1 if lines <= 2000 else 2)


def test_multibyte_and_inline_offsets_are_preserved() -> None:
    content = verified("汉" * 87382)
    result, offsets = chunks(content)
    assert "".join(result) == "汉" * 87382
    assert offsets == [0, 262143]
    assert project_preview(
        content, budget=ReadBudget(5, monotonic=lambda: 0)
    ).inline_continuation
    for offset in (-1, 1, 262147):
        with pytest.raises(ReadFailure):
            project_preview(
                content, offset=offset, budget=ReadBudget(5, monotonic=lambda: 0)
            )
    end = project_preview(
        content, offset=262146, budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert end.text == "" and end.next_offset is None


def test_json_is_redacted_and_serialized_as_plain_text() -> None:
    preview = project_preview(
        verified(
            '{"visible":"ok","token":123456789}',
            media_type="application/json",
            kind="evaluation_report",
        ),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert preview.text == '{\n  "visible": "ok",\n  "token": "[已脱敏]"\n}'
    assert all("must-not-display" not in field.value for field in preview.fields)


import json


@pytest.mark.parametrize(
    ("value",),
    [
        ({"message": "[" * 65 + "]" * 65, "secret": "private-value"},),
        ({"message": '"\\' + "[]" * 65, "secret": "private-value"},),
        ({"{}" * 65 + '"\\': "visible-ok", "secret": "private-value"},),
    ],
)
def test_shallow_json_lexical_variants_reach_the_preview(value) -> None:
    preview = project_preview(
        verified(
            json.dumps(value), media_type="application/json", kind="evaluation_report"
        ),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert preview.text is not None
    assert json.loads(preview.text) == (value | {"secret": "[已脱敏]"})
    assert preview.issues == ()


@pytest.mark.parametrize(("depth",), [(62,), (63,), (64,), (65,), (66,)])
def test_json_depth_gate_at_the_preview_consumer(depth: int) -> None:
    preview = project_preview(
        verified(
            "[" * depth + "0" + "]" * depth,
            media_type="application/json",
            kind="evaluation_report",
        ),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert (preview.text is not None) == (depth <= 64)
    if depth > 64:
        assert "json_depth" in {issue.code for issue in preview.issues}


@pytest.mark.parametrize(("newline", "next_offset"), [("\r\n", 6000), ("\u2028", 8000)])
def test_logical_newlines_preserve_the_line_limit(
    newline: str, next_offset: int
) -> None:
    content = verified(("x" + newline) * 2001)
    result, offsets = chunks(content)
    assert result == [("x" + newline) * 2000, "x" + newline]
    assert offsets == [0, next_offset]


@pytest.mark.parametrize(
    ("content",), [(b"\xff",), (b"visible\x00tail",), ("visible\x85tail".encode(),)]
)
def test_invalid_or_control_text_is_metadata_only(content: bytes) -> None:
    preview = project_preview(
        verified(content), budget=ReadBudget(5, monotonic=lambda: 0)
    )
    assert preview.text is None
    assert "preview_binary" in {issue.code for issue in preview.issues}


def test_json_artifact_reference_uri_is_hidden() -> None:
    preview = project_preview(
        verified(
            '{"patch_ref":{"uri":"file:///private/host-secret"},"visible":"ok"}',
            media_type="application/json",
            kind="evaluation_config",
        ),
        budget=ReadBudget(5, monotonic=lambda: 0),
    )
    assert preview.text is not None
    assert "host-secret" not in preview.text
    assert json.loads(preview.text)["visible"] == "ok"
