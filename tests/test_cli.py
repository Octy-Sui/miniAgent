import asyncio
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from miniagent.agents import AgentSpec
from miniagent.agents.default import DEFAULT_AGENT
from miniagent.cli import main as cli_main
from miniagent.cli.main import (
    _ForcedSignalCancellation,
    _run_agent_with_signals,
    _signal_exit_code,
    app,
)
from miniagent.runtime import (
    CancellationToken,
    RunResult,
    RunStatus,
    RunUsage,
    TerminationReason,
)


class SignalLoop:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.callbacks: dict[
            signal.Signals,
            tuple[Callable[..., None], tuple[Any, ...]],
        ] = {}
        self.removed_signals: list[signal.Signals] = []

    def create_future(self) -> asyncio.Future[signal.Signals]:
        return self.loop.create_future()

    def add_signal_handler(
        self,
        handled_signal: signal.Signals,
        callback: Callable[..., None],
        *args: Any,
    ) -> None:
        self.callbacks[handled_signal] = (callback, args)

    def remove_signal_handler(self, handled_signal: signal.Signals) -> bool:
        self.removed_signals.append(handled_signal)
        return True

    def send(self, handled_signal: signal.Signals) -> None:
        callback, args = self.callbacks[handled_signal]
        callback(*args)


@pytest.fixture
async def signal_loop(monkeypatch: pytest.MonkeyPatch) -> SignalLoop:
    loop = SignalLoop(asyncio.get_running_loop())
    monkeypatch.setattr(cli_main.asyncio, "get_running_loop", lambda: loop)
    return loop


def successful_result(*, separate_cache_pricing: bool = False) -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        run_id=uuid4(),
        status=RunStatus.SUCCEEDED,
        reason=TerminationReason.TASK_VERIFIED,
        output="answer",
        usage=RunUsage(
            input_tokens=132_935,
            cached_input_tokens=93_920,
            output_tokens=42_728,
            reasoning_output_tokens=14_382,
            total_tokens=175_663,
            cached_input_priced_separately=separate_cache_pricing,
            input_cost_usd=Decimal(
                "0.0156060" if separate_cache_pricing else "0.0531740"
            ),
            cached_input_cost_usd=Decimal(
                "0.0093920" if separate_cache_pricing else "0"
            ),
            output_cost_usd=Decimal("0.0512736"),
            cost_usd=Decimal(
                "0.0762716" if separate_cache_pricing else "0.1044476"
            ),
        ),
        started_at=now,
        finished_at=now,
    )


async def test_first_signal_requests_graceful_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    signal_loop: SignalLoop,
) -> None:
    expected_result = successful_result()

    async def wait_for_cancellation(
        agent: AgentSpec,
        task: str,
        workspace: Path,
        cancellation_token: CancellationToken,
    ) -> RunResult:
        await cancellation_token.wait()
        return expected_result

    monkeypatch.setattr(cli_main, "run_agent", wait_for_cancellation)
    run = asyncio.create_task(
        _run_agent_with_signals(DEFAULT_AGENT, "task", Path.cwd())
    )
    await asyncio.sleep(0)
    signal_loop.send(signal.SIGINT)

    result, received_signal = await run

    assert result is expected_result
    assert received_signal is signal.SIGINT
    assert signal_loop.removed_signals == [signal.SIGINT, signal.SIGTERM]


async def test_second_signal_forces_task_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    signal_loop: SignalLoop,
) -> None:
    started = asyncio.Event()

    async def block(
        agent: AgentSpec,
        task: str,
        workspace: Path,
        cancellation_token: CancellationToken,
    ) -> RunResult:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(cli_main, "run_agent", block)
    run = asyncio.create_task(
        _run_agent_with_signals(DEFAULT_AGENT, "task", Path.cwd())
    )
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


def test_successful_run_prints_cost_after_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_agent(
        agent: AgentSpec,
        task: str,
        workspace: Path,
        cancellation_token: CancellationToken,
    ) -> RunResult:
        return successful_result()

    monkeypatch.setattr(cli_main, "run_agent", run_agent)

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert invocation.stdout == (
        "answer\n"
        "Token usage: total=175663 input=132935 (+93920 cached) "
        "output=42728 (14382 reasoning)\n"
        "Cost: total=$0.10444760 input=$0.05317400 "
        "output=$0.05127360 USD\n"
    )


def test_separate_cache_pricing_prints_cache_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_agent(
        agent: AgentSpec,
        task: str,
        workspace: Path,
        cancellation_token: CancellationToken,
    ) -> RunResult:
        return successful_result(separate_cache_pricing=True)

    monkeypatch.setattr(cli_main, "run_agent", run_agent)

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert (
        "Cost: total=$0.07627160 uncached_input=$0.01560600 "
        "cached=$0.00939200 output=$0.05127360 USD\n"
        in invocation.stdout
    )
