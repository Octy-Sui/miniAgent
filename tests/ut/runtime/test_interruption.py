import pytest

from miniagent.runtime import (
    CancellationToken,
    InterruptionRequested,
    InterruptionToken,
)
from miniagent.runtime.cancellation import await_with_stop


@pytest.mark.parametrize(("remaining_ms",), [(-1,), (0,), (1,), (2,)])
def test_shared_deadline_does_not_refresh(remaining_ms):
    now = [1000.0]
    token = InterruptionToken(timeout_seconds=0.002, clock=lambda: now[0])
    token.request(2)
    first = token.deadline
    now[0] += (2 - remaining_ms) / 1000
    token.request(15)
    assert token.deadline == first
    assert token.remaining_seconds() == pytest.approx(max(0, remaining_ms / 1000))
    assert token.interruption.signal_number == 2


async def test_expired_interruption_never_starts_new_work():
    token = InterruptionToken(timeout_seconds=1, clock=lambda: 1)
    token.request(2)
    calls = []

    async def work():
        calls.append("forbidden")

    with pytest.raises(InterruptionRequested):
        await await_with_stop(work(), CancellationToken(), token)
    assert calls == []
