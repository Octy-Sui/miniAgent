# File   : snapshots.py
# Created: 2026-09-11 18:44:50
# Author : octy Sui

from collections.abc import Awaitable
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from miniagent.runtime.recovery import RunRecoveryContext
from miniagent.runtime.state import RunState


class SnapshotVersionConflictError(RuntimeError):
    def __init__(self, *, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Expected snapshot version {expected}, found {actual}")


class NewRunSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run_id: UUID
    event_sequence: int = Field(ge=0)
    state: RunState
    recovery_context: RunRecoveryContext | None = None

    @model_validator(mode="after")
    def validate_run_id(self) -> "NewRunSnapshot":
        if self.state.identity.run_id != self.run_id:
            raise ValueError("Snapshot and state must belong to the same run")
        if (
            self.recovery_context is not None
            and self.recovery_context.workspace_spec.run_id != self.run_id
        ):
            raise ValueError("Snapshot recovery context must belong to the same run")
        return self


class RunSnapshot(NewRunSnapshot):
    snapshot_id: UUID
    version: int = Field(ge=1)
    created_at: AwareDatetime


class SnapshotStore(Protocol):
    def save(
        self,
        snapshot: NewRunSnapshot,
        *,
        expected_version: int,
    ) -> Awaitable[RunSnapshot]: ...

    async def load_latest(self, run_id: UUID) -> RunSnapshot | None: ...
