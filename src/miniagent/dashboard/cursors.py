import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.parameters import (
    EventFilters,
    Parameters,
    RunFilters,
    UtcDatetime,
)
from miniagent.dashboard.reads import ReadFailure
from miniagent.runtime.state import RunStatus


class PageBoundary(Parameters):
    run_id: UUID
    as_of_sequence: int = Field(ge=0)
    latest_snapshot_version: int = Field(ge=0)
    run_status: RunStatus
    read_at: UtcDatetime


class CursorTimes(Parameters):
    version: Literal[1] = 1
    issued_at: UtcDatetime
    expires_at: UtcDatetime

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        if (self.expires_at - self.issued_at).total_seconds() != LIMITS.cursor_seconds:
            raise ValueError("cursor_lifetime")
        return self


class RunPosition(Parameters):
    created_at: UtcDatetime
    run_id: UUID


class RunListCursor(CursorTimes):
    purpose: Literal["runs"] = "runs"
    filter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    position: RunPosition


class EventCursor(CursorTimes):
    purpose: Literal["events"] = "events"
    filter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    boundary: PageBoundary
    after_sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        if self.after_sequence > self.boundary.as_of_sequence:
            raise ValueError("cursor_position")
        return self


class SourceCursor(CursorTimes):
    purpose: Literal["source"] = "source"
    boundary: PageBoundary
    return_filters: EventFilters = EventFilters()
    return_grouped: bool = False


class ManifestCursor(CursorTimes):
    purpose: Literal["manifests"] = "manifests"
    boundary: PageBoundary
    after_id: UUID


class PreviewCursor(CursorTimes):
    purpose: Literal["preview"] = "preview"
    boundary: PageBoundary
    artifact_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_version: Literal[1] = 1
    offset: int = Field(ge=0)


type DashboardCursor = Annotated[
    RunListCursor | EventCursor | SourceCursor | ManifestCursor | PreviewCursor,
    Field(discriminator="purpose"),
]
_ADAPTER = TypeAdapter(DashboardCursor)
_ENCODING = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stable_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _base64(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).rstrip(b"=").decode("ascii")


class CursorSigner:
    def __init__(self, key: bytes, *, now: Callable[[], datetime]) -> None:
        self.key = key
        self.now = now

    def encode(self, cursor: DashboardCursor) -> str:
        raw = _stable_json(cursor.model_dump(mode="json"))
        token = _base64(raw) + "." + _base64(hmac.digest(self.key, raw, "sha256"))
        if len(token) > LIMITS.cursor_bytes:
            raise ReadFailure(422, "cursor_length")
        return token

    def decode(self, token: str, *, purpose: str) -> DashboardCursor:
        if len(token) > LIMITS.cursor_bytes:
            raise ReadFailure(422, "cursor_length")
        if not _ENCODING.fullmatch(token):
            raise ReadFailure(422, "cursor_invalid")
        payload, signature = token.split(".")
        try:
            raw = base64.b64decode(
                payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True
            )
            signed = base64.b64decode(
                signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True
            )
            if _base64(raw) != payload or _base64(signed) != signature:
                raise ReadFailure(422, "cursor_invalid")
            if not hmac.compare_digest(hmac.digest(self.key, raw, "sha256"), signed):
                raise ReadFailure(422, "cursor_signature")
            cursor = _ADAPTER.validate_python(parse_json(raw))
        except (binascii.Error, ValidationError, ContentError) as exc:
            raise ReadFailure(422, "cursor_invalid") from exc
        if cursor.purpose != purpose:
            raise ReadFailure(422, "cursor_purpose")
        now = self.now()
        if now < cursor.issued_at:
            raise ReadFailure(422, "cursor_future")
        if now >= cursor.expires_at:
            raise ReadFailure(422, "cursor_expired")
        return cursor


def filter_digest(filters: RunFilters | EventFilters) -> str:
    return hashlib.sha256(_stable_json(filters.model_dump(mode="json"))).hexdigest()
