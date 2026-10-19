from datetime import UTC
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from miniagent.dashboard.reads import ReadFailure
from miniagent.runtime.events import RunEventType
from miniagent.runtime.state import RunStatus
from miniagent.storage.codecs import encode_datetime

UtcDatetime = Annotated[
    AwareDatetime,
    AfterValidator(lambda value: value.astimezone(UTC)),
    PlainSerializer(encode_datetime, return_type=str, when_used="json"),
]


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RunFilters(Parameters):
    statuses: tuple[RunStatus, ...] = ()
    model: str | None = None
    workspace: Literal["local", "docker", "unknown"] | None = None
    created_from: UtcDatetime | None = None
    created_to: UtcDatetime | None = None
    run_id: UUID | None = None
    task: str | None = Field(default=None, max_length=200)

    @field_validator("statuses")
    @classmethod
    def normalize_statuses(cls, values: tuple[RunStatus, ...]) -> tuple[RunStatus, ...]:
        return tuple(sorted(set(values)))

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_from >= self.created_to
        ):
            raise ValueError("invalid_time_range")
        return self


class RunListParams(Parameters):
    filters: RunFilters
    page_size: Literal[10, 25, 50, 100] = 25
    cursor: str | None = None


class EventFilters(Parameters):
    types: tuple[RunEventType, ...] = ()
    step: int | None = Field(default=None, ge=1)
    tool_name: str | None = None
    failures_only: bool = False

    @field_validator("types")
    @classmethod
    def normalize_types(
        cls, values: tuple[RunEventType, ...]
    ) -> tuple[RunEventType, ...]:
        return tuple(sorted(set(values)))


class EventParams(Parameters):
    filters: EventFilters
    limit: int = Field(default=100, ge=1, le=200)
    cursor: str | None = None
    boundary: str | None = None
    event_id: UUID | None = None
    grouped: bool = False

    @model_validator(mode="after")
    def validate_locator(self) -> Self:
        if self.cursor is not None and self.event_id is not None:
            raise ValueError("locator_with_cursor")
        return self


class SnapshotParams(Parameters):
    boundary: str
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.end is not None and (
            self.end < self.start or self.end - self.start > 100
        ):
            raise ValueError("invalid_message_range")
        return self


class ArtifactParams(Parameters):
    boundary: str
    cursor: str | None = None
    offset: int | None = Field(default=None, ge=0)


class WorkspaceParams(Parameters):
    boundary: str | None = None
    cursor: str | None = None
    patch: UUID | None = None


def _query(
    pairs: list[tuple[str, str]], *, allowed: set[str], multiple: str
) -> dict[str, object]:
    values: dict[str, object] = {}
    selections: list[str] = []
    for key, value in pairs:
        if key not in allowed or (key != multiple and key in values):
            raise ReadFailure(422, "parameters_invalid")
        if key == multiple:
            selections.append(value)
        else:
            values[key] = value
    values[multiple] = selections
    return values


def run_parameters(pairs: list[tuple[str, str]]) -> RunListParams:
    values = _query(
        pairs,
        allowed={
            "status",
            "model",
            "workspace",
            "from",
            "to",
            "run_id",
            "task",
            "page_size",
            "cursor",
        },
        multiple="status",
    )
    values = {key: value for key, value in values.items() if value != ""}
    statuses = values["status"]
    assert isinstance(statuses, list)
    values["status"] = [value for value in statuses if value != ""]
    filters = {
        (
            {"status": "statuses", "from": "created_from", "to": "created_to"}.get(
                key, key
            )
        ): value
        for key, value in values.items()
        if key not in ("page_size", "cursor")
    }
    try:
        return RunListParams.model_validate(
            {
                "filters": filters,
                "page_size": int(str(values.get("page_size", 25))),
                "cursor": values.get("cursor"),
            }
        )
    except ValueError as exc:
        raise ReadFailure(422, "parameters_invalid") from exc


def event_parameters(pairs: list[tuple[str, str]]) -> EventParams:
    values = _query(
        pairs,
        allowed={
            "type",
            "step",
            "tool_name",
            "failures_only",
            "limit",
            "cursor",
            "boundary",
            "event_id",
            "grouped",
        },
        multiple="type",
    )
    filter_keys = {"type", "step", "tool_name", "failures_only"}
    filters = {
        ("types" if key == "type" else key): value
        for key, value in values.items()
        if key in filter_keys
    }
    try:
        return EventParams.model_validate(
            {
                "filters": filters,
                **{
                    key: value
                    for key, value in values.items()
                    if key not in filter_keys
                },
            }
        )
    except ValueError as exc:
        raise ReadFailure(422, "parameters_invalid") from exc
