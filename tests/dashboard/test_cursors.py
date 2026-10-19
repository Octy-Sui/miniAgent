import base64
import hashlib
import hmac
from datetime import UTC, datetime

from miniagent.dashboard.cursors import CursorSigner, SourceCursor

KEY = b"independent-vector-key-32-bytes!!"
NOW = datetime(2026, 10, 19, tzinfo=UTC)


def test_cursor_matches_independent_hmac_vector() -> None:
    payload = b'{"boundary":{"as_of_sequence":240,"latest_snapshot_version":3,"read_at":"2026-10-19T00:00:00.000000Z","run_id":"00000000-0000-0000-0000-000000000001","run_status":"running"},"expires_at":"2026-10-19T00:30:00.000000Z","issued_at":"2026-10-19T00:00:00.000000Z","purpose":"source","version":1}'
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = base64.urlsafe_b64encode(
        hmac.digest(KEY, payload, hashlib.sha256)
    ).rstrip(b"=")
    token = (encoded + b"." + signature).decode()
    signer = CursorSigner(KEY, now=lambda: NOW)
    cursor = signer.decode(token, purpose="source")
    assert isinstance(cursor, SourceCursor)
    assert cursor.boundary.as_of_sequence == 240
    assert signer.encode(cursor) == token


def sign_payload(payload: dict) -> str:
    import json

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return (
        base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        + "."
        + base64.urlsafe_b64encode(hmac.digest(KEY, raw, "sha256"))
        .rstrip(b"=")
        .decode()
    )


def source_payload() -> dict:
    return {
        "version": 1,
        "purpose": "source",
        "issued_at": "2026-10-19T00:00:00Z",
        "expires_at": "2026-10-19T00:30:00Z",
        "boundary": {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "as_of_sequence": 240,
            "latest_snapshot_version": 3,
            "run_status": "running",
            "read_at": "2026-10-19T00:00:00Z",
        },
    }


import pytest

from miniagent.dashboard.reads import ReadFailure


@pytest.mark.parametrize(
    ("change",),
    [
        ({"version": 2},),
        ({"purpose": "events"},),
        ({"issued_at": "2026-10-19T00:00:01Z"},),
        ({"expires_at": "2026-10-19T00:29:59Z"},),
        ({"unrecognized": "input-secret"},),
        ({"issued_at": "2026-10-19T00:00:00"},),
    ],
)
def test_signed_cursor_rejects_semantic_misuse(change: dict) -> None:
    with pytest.raises(ReadFailure):
        CursorSigner(KEY, now=lambda: NOW).decode(
            sign_payload(source_payload() | change), purpose="source"
        )


@pytest.mark.parametrize(
    ("now", "valid"),
    [
        (datetime(2026, 10, 19, tzinfo=UTC), True),
        (datetime(2026, 10, 19, 0, 29, 59, 999999, tzinfo=UTC), True),
        (datetime(2026, 10, 19, 0, 30, tzinfo=UTC), False),
    ],
)
def test_cursor_time_boundaries(now: datetime, valid: bool) -> None:
    signer = CursorSigner(KEY, now=lambda: now)
    if valid:
        assert signer.decode(sign_payload(source_payload()), purpose="source")
    else:
        with pytest.raises(ReadFailure, match="cursor_expired"):
            signer.decode(sign_payload(source_payload()), purpose="source")


@pytest.mark.parametrize(
    ("boundary_change",),
    [
        ({"as_of_sequence": -1},),
        ({"latest_snapshot_version": -1},),
        ({"run_id": "bad-id"},),
    ],
)
def test_signed_boundary_rejects_invalid_identity(boundary_change: dict) -> None:
    payload = source_payload()
    payload["boundary"] |= boundary_change
    with pytest.raises(ReadFailure):
        CursorSigner(KEY, now=lambda: NOW).decode(
            sign_payload(payload), purpose="source"
        )


@pytest.mark.parametrize(
    ("token",), [("A" * 4097,), ("a.b.c",), ("非法.签名",), ("a=.b",)]
)
def test_invalid_cursor_encoding(token: str) -> None:
    with pytest.raises(ReadFailure):
        CursorSigner(KEY, now=lambda: NOW).decode(token, purpose="source")


@pytest.mark.parametrize(
    ("purpose", "fields"),
    [
        ("events", {"filter_hash": "a" * 64, "after_sequence": 200}),
        (
            "runs",
            {
                "filter_hash": "b" * 64,
                "position": {
                    "created_at": "2026-10-18T00:00:00Z",
                    "run_id": "00000000-0000-0000-0000-000000000001",
                },
            },
        ),
        ("manifests", {"after_id": "00000000-0000-0000-0000-000000000002"}),
        (
            "preview",
            {
                "artifact_id": "00000000-0000-0000-0000-000000000002",
                "sha256": "c" * 64,
                "redaction_version": 1,
                "offset": 0,
            },
        ),
    ],
)
def test_all_cursor_purposes_roundtrip(purpose: str, fields: dict) -> None:
    payload = source_payload() | {"purpose": purpose} | fields
    if purpose == "runs":
        del payload["boundary"]
    signer = CursorSigner(KEY, now=lambda: NOW)
    cursor = signer.decode(sign_payload(payload), purpose=purpose)
    assert cursor.purpose == purpose
    assert signer.decode(signer.encode(cursor), purpose=purpose) == cursor
    with pytest.raises(ReadFailure):
        signer.decode(sign_payload(payload), purpose="source")


def test_event_position_cannot_exceed_boundary() -> None:
    payload = source_payload() | {
        "purpose": "events",
        "filter_hash": "a" * 64,
        "after_sequence": 241,
    }
    with pytest.raises(ReadFailure):
        CursorSigner(KEY, now=lambda: NOW).decode(
            sign_payload(payload), purpose="events"
        )


def test_cursor_key_restart_and_tampering_are_rejected() -> None:
    token = sign_payload(source_payload())
    with pytest.raises(ReadFailure):
        CursorSigner(b"different-process-key", now=lambda: NOW).decode(
            token, purpose="source"
        )
    with pytest.raises(ReadFailure):
        CursorSigner(KEY, now=lambda: NOW).decode("X" + token[1:], purpose="source")


def test_filter_digest_contains_no_literal_search_text() -> None:
    from miniagent.dashboard.cursors import filter_digest
    from miniagent.dashboard.parameters import RunFilters

    digest = filter_digest(
        RunFilters(statuses=("running", "created"), task="literal % done")
    )
    assert digest == filter_digest(
        RunFilters(statuses=("created", "running", "running"), task="literal % done")
    )
    assert len(digest) == 64
    assert digest != filter_digest(RunFilters(task="different"))
