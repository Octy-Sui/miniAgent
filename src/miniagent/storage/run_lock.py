import errno
import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Self
from uuid import UUID


class RunInUseError(RuntimeError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"RUN_IN_USE: run_{run_id.hex}")


class RunLock:
    def __init__(self, database: Path, run_id: UUID) -> None:
        self.database = database.expanduser().resolve()
        self.run_id = run_id
        self._fd: int | None = None

    def __enter__(self) -> Self:
        if self._fd is not None:
            raise RuntimeError("Run lock is already held by this context")
        directory = self.database.parent / ".miniagent-run-locks"
        directory.mkdir(parents=True, exist_ok=True)
        lock_file = directory / f"{self.run_id.hex}.lock"
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            os.set_inheritable(fd, False)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise RunInUseError(self.run_id) from exc
            exc.add_note(f"Acquiring run lock {lock_file}")
            raise
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
