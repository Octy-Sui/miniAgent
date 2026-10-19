import asyncio
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from time import monotonic
from typing import Literal

from miniagent.runtime.progress import InterruptionInfo


class InterruptionRequested(Exception):
    pass


class InterruptionToken:
    def __init__(
        self, timeout_seconds: float = 30.0, clock: Callable[[], float] = monotonic
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Interruption timeout must be finite and positive")
        self._timeout = timeout_seconds
        self._clock = clock
        self._event = asyncio.Event()
        self._deadline: float | None = None
        self._interruption: InterruptionInfo | None = None

    def request(self, signal_number: Literal[2, 15]) -> None:
        if signal_number not in (2, 15):
            raise ValueError("Interruption supports only SIGINT and SIGTERM")
        if self.is_requested:
            return
        self._deadline = self._clock() + self._timeout
        self._interruption = InterruptionInfo(
            signal_number=signal_number, requested_at=datetime.now(UTC)
        )
        self._event.set()

    @property
    def is_requested(self) -> bool:
        return self._event.is_set()

    @property
    def deadline(self) -> float | None:
        return self._deadline

    @property
    def interruption(self) -> InterruptionInfo:
        if self._interruption is None:
            raise ValueError("Interruption has not been requested")
        return self._interruption

    def remaining_seconds(self) -> float:
        return (
            max(0.0, self._deadline - self._clock())
            if self._deadline is not None
            else self._timeout
        )

    async def wait(self) -> None:
        await self._event.wait()


_CURRENT: ContextVar[InterruptionToken | None] = ContextVar(
    "miniagent_interruption", default=None
)


@contextmanager
def interruption_scope(token: InterruptionToken | None) -> Iterator[None]:
    previous = _CURRENT.set(token)
    try:
        yield
    finally:
        _CURRENT.reset(previous)


def current_interruption_token() -> InterruptionToken | None:
    return _CURRENT.get()


def raise_if_interrupted() -> None:
    token = current_interruption_token()
    if token is not None and token.is_requested:
        raise InterruptionRequested
