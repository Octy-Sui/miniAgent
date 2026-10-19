import asyncio
import getpass
import os
import stat
import sys
from datetime import UTC, datetime

import orjson
import typer

from miniagent.policy import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)


class ConsoleApprovalHandler:
    def __init__(self) -> None:
        self._input = b""

    async def _confirm(self) -> bool:
        loop = asyncio.get_running_loop()
        descriptor = sys.stdin.fileno()
        regular_file = stat.S_ISREG(os.fstat(descriptor).st_mode)
        while True:
            typer.echo("Allow this tool call? [y/N]: ", nl=False)
            while b"\n" not in self._input:
                if regular_file:
                    await asyncio.sleep(0)
                    data = os.read(descriptor, 4096)
                else:
                    readable = loop.create_future()

                    def ready(readable: asyncio.Future[None] = readable) -> None:
                        if not readable.done():
                            readable.set_result(None)

                    loop.add_reader(descriptor, ready)
                    try:
                        await readable
                        data = os.read(descriptor, 4096)
                    finally:
                        loop.remove_reader(descriptor)
                if not data:
                    if not self._input:
                        return False
                    self._input += b"\n"
                    break
                self._input += data
            line, self._input = self._input.split(b"\n", 1)
            answer = line.strip().lower()
            if answer in (b"y", b"yes"):
                return True
            if answer in (b"", b"n", b"no"):
                return False
            typer.echo("Please enter y or n.")

    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution:
        typer.echo(
            f"Approval required ({decision.risk}): {decision.input_summary}",
            err=True,
        )
        typer.echo(
            orjson.dumps(
                decision.request.arguments,
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            ).decode(),
            err=True,
        )
        approved = await self._confirm()
        return ApprovalResolution(
            approval_id=request.approval_id,
            outcome=(
                ApprovalOutcome.APPROVED if approved else ApprovalOutcome.REJECTED
            ),
            actor_kind="human",
            actor_id=getpass.getuser(),
            reason="Approved in the CLI" if approved else "Rejected in the CLI",
            resolved_at=datetime.now(UTC),
        )
