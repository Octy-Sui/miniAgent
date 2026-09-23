import asyncio
import getpass
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
        approved = await asyncio.to_thread(
            typer.confirm,
            "Allow this tool call?",
            default=False,
        )
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
