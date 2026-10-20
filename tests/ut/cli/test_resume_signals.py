import asyncio

import pytest

from miniagent.cli.signals import run_with_signals


async def test_bare_cancelled_error_is_not_a_signal_or_suspension():
    async def operation(cancellation, interruption):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_with_signals(operation)
