import asyncio
import signal
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from typer.testing import CliRunner

from miniagent.agents.default import DEFAULT_AGENT
from miniagent.cli.main import (
    _ForcedSignalCancellation,
    _run_agent_with_signals,
    _signal_exit_code,
    app,
)
from miniagent.runtime import (
    RunResult,
    RunStatus,
    RunUsage,
    TerminationReason,
)


class CliCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.callbacks: dict[signal.Signals, tuple[Any, tuple[Any, ...]]] = {}
        self.loop = asyncio.get_running_loop()
        self.signal_loop = Mock()
        self.signal_loop.create_future.side_effect = self.loop.create_future
        self.signal_loop.add_signal_handler.side_effect = self._register_signal
        self.signal_loop.remove_signal_handler.return_value = True

    def _register_signal(
        self,
        handled_signal: signal.Signals,
        callback: Any,
        *args: Any,
    ) -> None:
        self.callbacks[handled_signal] = (callback, args)

    def _send_signal(self, handled_signal: signal.Signals) -> None:
        callback, args = self.callbacks[handled_signal]
        callback(*args)

    async def test_first_signal_requests_graceful_cancellation(self) -> None:
        expected_result = Mock(spec=RunResult)

        async def wait_for_cancellation(
            *args: Any,
            **kwargs: Any,
        ) -> RunResult:
            cancellation_token = args[3]
            await cancellation_token.wait()
            return expected_result

        with (
            patch(
                "miniagent.cli.main.asyncio.get_running_loop",
                return_value=self.signal_loop,
            ),
            patch(
                "miniagent.cli.main.run_agent",
                new=AsyncMock(side_effect=wait_for_cancellation),
            ),
        ):
            run = asyncio.create_task(
                _run_agent_with_signals(DEFAULT_AGENT, "task", Path.cwd())
            )
            await asyncio.sleep(0)
            self._send_signal(signal.SIGINT)
            result, received_signal = await run

        self.assertIs(result, expected_result)
        self.assertIs(received_signal, signal.SIGINT)
        self.signal_loop.remove_signal_handler.assert_any_call(signal.SIGINT)
        self.signal_loop.remove_signal_handler.assert_any_call(signal.SIGTERM)

    async def test_second_signal_forces_task_cancellation(self) -> None:
        started = asyncio.Event()

        async def block(*args: Any, **kwargs: Any) -> RunResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        with (
            patch(
                "miniagent.cli.main.asyncio.get_running_loop",
                return_value=self.signal_loop,
            ),
            patch(
                "miniagent.cli.main.run_agent",
                new=AsyncMock(side_effect=block),
            ),
        ):
            run = asyncio.create_task(
                _run_agent_with_signals(DEFAULT_AGENT, "task", Path.cwd())
            )
            await started.wait()
            self._send_signal(signal.SIGTERM)
            self._send_signal(signal.SIGTERM)

            with self.assertRaises(_ForcedSignalCancellation) as context:
                await run

        self.assertIs(context.exception.received_signal, signal.SIGTERM)

    def test_signal_exit_codes_follow_shell_convention(self) -> None:
        self.assertEqual(_signal_exit_code(signal.SIGINT), 130)
        self.assertEqual(_signal_exit_code(signal.SIGTERM), 143)


class CliOutputTests(unittest.TestCase):
    def test_successful_run_prints_cost_after_output(self) -> None:
        now = datetime.now(UTC)
        result = RunResult(
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
                input_cost_usd=Decimal("0.0156060"),
                cached_input_cost_usd=Decimal("0.0093920"),
                output_cost_usd=Decimal("0.0512736"),
                cost_usd=Decimal("0.0762716"),
            ),
            started_at=now,
            finished_at=now,
        )

        with patch(
            "miniagent.cli.main._run_agent_with_signals",
            new=AsyncMock(return_value=(result, None)),
        ):
            invocation = CliRunner().invoke(app, ["task"])

        self.assertEqual(invocation.exit_code, 0)
        self.assertEqual(
            invocation.stdout,
            "answer\n"
            "Token usage: total=175663 input=132935 (+93920 cached) "
            "output=42728 (14382 reasoning)\n"
            "Cost: total=$0.07627160 input=$0.01560600 "
            "cached=$0.00939200 output=$0.05127360 USD\n",
        )


if __name__ == "__main__":
    unittest.main()
