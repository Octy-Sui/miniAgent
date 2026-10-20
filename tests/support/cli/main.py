import asyncio
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactRef
from miniagent.cli import main as cli_main
from miniagent.cli.application import (
    ApplicationResult,
    FinalizationError,
)
from miniagent.cli.bootstrap import BootstrapConfig
from miniagent.runtime import (
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
            cost_usd=Decimal("0.0762716" if separate_cache_pricing else "0.1044476"),
        ),
        started_at=now,
        finished_at=now,
    )


def stub_cli_run(
    monkeypatch: pytest.MonkeyPatch,
    result: RunResult,
    configs: list[BootstrapConfig] | None = None,
    patch_artifact: ArtifactRef | None = None,
    finalization_errors: tuple[FinalizationError, ...] = (),
) -> None:
    async def execute(
        config: BootstrapConfig, *, cancellation_token, interruption_token
    ):
        if configs is not None:
            configs.append(config)
        artifacts = (patch_artifact,) if patch_artifact is not None else ()
        return ApplicationResult(
            run_result=result,
            artifacts=artifacts,
            finalization_errors=finalization_errors,
        )

    monkeypatch.setattr(cli_main, "execute_new_run", execute)
