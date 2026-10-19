from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _Identity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HostProcessIdentity(_Identity):
    schema_version: Literal[1] = 1
    role: Literal["local_command", "docker_helper", "harness"]
    attempt_id: UUID
    execution_id: UUID
    pid: int = Field(gt=0)
    create_time: float = Field(gt=0, allow_inf_nan=False)
    host_boot_id: str = Field(min_length=1)
    pgid: int = Field(gt=0)
    sid: int = Field(gt=0)


class GuestProcessIdentity(_Identity):
    pid: int = Field(gt=0)
    pgid: int = Field(gt=0)
    sid: int = Field(gt=0)
    boot_id: str = Field(min_length=1)
    start_ticks: int = Field(ge=0)


class DockerExecIdentity(_Identity):
    schema_version: Literal[1] = 1
    role: Literal["docker_exec"] = "docker_exec"
    attempt_id: UUID
    execution_id: UUID
    helper_execution_id: UUID
    daemon_id: str = Field(min_length=1)
    container_id: str = Field(min_length=1)
    exec_id: str = Field(min_length=1)
    start_permitted: bool
    started_observed: bool
    guest: GuestProcessIdentity | None = None


ExecutionIdentity = Annotated[
    HostProcessIdentity | DockerExecIdentity, Field(discriminator="role")
]


class ExecutionEvidence(_Identity):
    execution_id: UUID
    identity: ExecutionIdentity
    outcome: Literal["ended", "active", "unknown"]
    started_observed: bool
    group_ended: bool
    issuer_ended: bool


class ManagedExecutionError(RuntimeError):
    pass


class ExecutionObserver(Protocol):
    @property
    def attempt_id(self) -> UUID: ...

    async def identified(
        self,
        identity: ExecutionIdentity,
        *,
        permission: Literal["observe", "create_exec", "start_exec"] = "observe",
    ) -> None: ...

    async def ended(self, evidence: ExecutionEvidence) -> None: ...


_OBSERVER: ContextVar[ExecutionObserver | None] = ContextVar(
    "miniagent_execution_observer", default=None
)


@contextmanager
def execution_scope(observer: ExecutionObserver) -> Iterator[None]:
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def current_execution_observer() -> ExecutionObserver | None:
    return _OBSERVER.get()
