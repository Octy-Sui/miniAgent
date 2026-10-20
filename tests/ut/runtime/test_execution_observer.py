import asyncio

from miniagent.environments.managed_execution import (
    current_execution_observer,
    execution_scope,
)


async def test_execution_scope_isolated_and_reset_on_exception():
    """TC-U6: parallel scopes and exceptional unwinding cannot leak observers."""
    first, second = object(), object()
    ready = asyncio.Event()
    observations = []

    async def work(observer, raises):
        with execution_scope(observer):
            ready.set()
            await asyncio.sleep(0)
            observations.append(current_execution_observer())
            if raises:
                raise ValueError("scope exit")
        assert current_execution_observer() is None

    results = await asyncio.gather(
        work(first, True), work(second, False), return_exceptions=True
    )
    assert observations == [first, second]
    assert isinstance(results[0], ValueError)
    assert current_execution_observer() is None
