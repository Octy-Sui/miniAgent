# File   : approvals.py
# Created: 2026-09-17 17:09:38
# Author : octy Sui

from typing import Protocol

from miniagent.policy.models import (
    ApprovalRequest,
    ApprovalResolution,
    PolicyDecision,
)


class ApprovalHandler(Protocol):
    async def resolve(
        self,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> ApprovalResolution: ...
