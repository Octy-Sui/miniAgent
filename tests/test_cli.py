import asyncio
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from miniagent.artifacts import ArtifactRef
from miniagent.cli import main as cli_main
from miniagent.cli.bootstrap import BootstrapConfig, BootstrappedAgent
from miniagent.cli.main import (
    _ForcedSignalCancellation,
    _signal_exit_code,
    app,
    run_with_signals,
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


def stub_cli_run(
    monkeypatch: pytest.MonkeyPatch,
    result: RunResult,
    configs: list[BootstrapConfig] | None = None,
    patch_artifact: ArtifactRef | None = None,
) -> None:
    class StubAgent:
        def __init__(self) -> None:
            self.patch_artifact = patch_artifact

    @asynccontextmanager
    async def bootstrap(
        config: BootstrapConfig,
    ) -> AsyncIterator[BootstrappedAgent]:
        if configs is not None:
            configs.append(config)
        yield cast(BootstrappedAgent, StubAgent())

    async def run_with_signals(
        agent: BootstrappedAgent,
    ) -> tuple[RunResult, signal.Signals | None]:
        return result, None

    monkeypatch.setattr(cli_main, "bootstrap", bootstrap)
    monkeypatch.setattr(cli_main, "run_with_signals", run_with_signals)


async def test_first_signal_requests_graceful_cancellation(
    signal_loop: SignalLoop,
) -> None:
    expected_result = successful_result()

    class WaitingAgent:
        async def run(self, cancellation_token: CancellationToken) -> RunResult:
            await cancellation_token.wait()
            return expected_result

    run = asyncio.create_task(
        run_with_signals(cast(BootstrappedAgent, WaitingAgent()))
    )
    await asyncio.sleep(0)
    signal_loop.send(signal.SIGINT)

    result, received_signal = await run

    assert result is expected_result
    assert received_signal is signal.SIGINT
    assert signal_loop.removed_signals == [signal.SIGINT, signal.SIGTERM]


async def test_second_signal_forces_task_cancellation(
    signal_loop: SignalLoop,
) -> None:
    started = asyncio.Event()

    class BlockingAgent:
        async def run(self, cancellation_token: CancellationToken) -> RunResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    run = asyncio.create_task(
        run_with_signals(cast(BootstrappedAgent, BlockingAgent()))
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
    stub_cli_run(monkeypatch, successful_result())

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
    stub_cli_run(monkeypatch, successful_result(separate_cache_pricing=True))

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert (
        "Cost: total=$0.07627160 uncached_input=$0.01560600 "
        "cached=$0.00939200 output=$0.05127360 USD\n"
        in invocation.stdout
    )


def test_successful_run_prints_persisted_patch_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_cli_run(
        monkeypatch,
        successful_result(),
        patch_artifact=ArtifactRef(uri="file:///artifacts/changes.patch"),
    )

    invocation = CliRunner().invoke(app, ["task"])

    assert invocation.exit_code == 0
    assert "Patch: file:///artifacts/changes.patch\n" in invocation.stdout


def test_cli_forwards_docker_workspace_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs: list[BootstrapConfig] = []
    stub_cli_run(monkeypatch, successful_result(), configs)

    invocation = CliRunner().invoke(
        app,
        ["task", "--workspace", "docker", "--docker-image", "task-image"],
    )

    assert invocation.exit_code == 0
    assert len(configs) == 1
    assert configs[0].workspace_kind is cli_main.WorkspaceKind.DOCKER
    assert configs[0].docker_image == "task-image"
