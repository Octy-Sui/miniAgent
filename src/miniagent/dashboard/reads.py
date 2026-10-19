import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ReadIssue:
    code: str
    message: str
    subject_id: UUID | None = None
    field_path: tuple[str | int, ...] = ()


class ReadFailure(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code
        super().__init__(code)


@dataclass
class ReadBudget:
    deadline: float
    remaining_bytes: int = 67108864
    monotonic: Callable[[], float] = time.monotonic

    def check(self) -> None:
        if self.monotonic() >= self.deadline:
            raise ReadFailure(503, "read_deadline")

    def reserve(self, size: int) -> bool:
        self.check()
        if size < 0:
            raise ValueError("negative read reservation")
        if size > self.remaining_bytes:
            return False
        self.remaining_bytes -= size
        return True
