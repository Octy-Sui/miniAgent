import asyncio
import signal

import pytest

from miniagent.cli.application import (
    ApplicationResult,
)
from miniagent.cli.main import (
    _ForcedSignalCancellation,
    _signal_exit_code,
    run_with_signals,
)
from miniagent.runtime import (
    CancellationToken,
    InterruptionToken,
)
from tests.support.cli.main import SignalLoop, successful_result


async def test_first_signal_requests_graceful_cancellation(
    signal_loop: SignalLoop,
) -> None:
    expected_result = successful_result()

    async def operation(
        cancellation_token: CancellationToken, interruption_token: InterruptionToken
    ):
        await interruption_token.wait()
        assert not cancellation_token.is_cancelled
        return ApplicationResult(run_result=expected_result)

    run = asyncio.create_task(run_with_signals(operation))
    await asyncio.sleep(0)
    signal_loop.send(signal.SIGINT)

    result, received_signal = await run

    assert result.run_result is expected_result
    assert received_signal is signal.SIGINT
    assert signal_loop.removed_signals == [signal.SIGINT, signal.SIGTERM]


async def test_second_signal_forces_task_cancellation(
    signal_loop: SignalLoop,
) -> None:
    started = asyncio.Event()

    async def operation(
        cancellation_token: CancellationToken, interruption_token: InterruptionToken
    ):
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    run = asyncio.create_task(run_with_signals(operation))
    await started.wait()
    signal_loop.send(signal.SIGTERM)
    signal_loop.send(signal.SIGTERM)

    with pytest.raises(_ForcedSignalCancellation) as exc_info:
        await run

    assert exc_info.value.received_signal is signal.SIGTERM


@pytest.mark.parametrize(
    ("received_signal", "expected"),
    [
        (signal.SIGINT, 130),
        (signal.SIGTERM, 143),
    ],
)
def test_signal_exit_code_follows_shell_convention(
    received_signal: signal.Signals,
    expected: int,
) -> None:
    assert _signal_exit_code(received_signal) == expected
