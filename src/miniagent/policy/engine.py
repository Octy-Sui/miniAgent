from collections.abc import Sequence

from miniagent.policy.contracts import (
    PolicyContext,
    PolicyEffect,
    PolicyEvaluation,
    PolicyRequest,
    RiskLevel,
)
from miniagent.policy.rules import DEFAULT_RULES, PolicyRule, RuleMatch


class DefaultPolicyEngine:
    def __init__(self, rules: Sequence[PolicyRule] = DEFAULT_RULES) -> None:
        self.rules = tuple(rules)

    @property
    def revision(self) -> str:
        return "default-v4"

    def evaluate(self, request: PolicyRequest) -> PolicyEvaluation:
        context = PolicyContext(
            workspace_id=request.workspace_id,
            workspace_kind=request.workspace_kind,
            revision=request.execution_context_digest,
        )
        matches = [
            match
            for rule in self.rules
            if (match := rule.evaluate(request.operation, context)) is not None
        ]
        if not matches:
            raise ValueError("No policy rule handled the operation")
        return _merge_matches(matches)


def _merge_matches(matches: list[RuleMatch]) -> PolicyEvaluation:
    effect_priority = {
        PolicyEffect.ALLOW: 0,
        PolicyEffect.APPROVAL: 1,
        PolicyEffect.DENY: 2,
    }
    effect = max(matches, key=lambda match: effect_priority[match.effect]).effect
    decisive = [match for match in matches if match.effect is effect]
    risk_priority = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.UNKNOWN: 3,
    }
    selected = max(decisive, key=lambda match: risk_priority[match.risk])
    return PolicyEvaluation(
        effect=effect,
        risk=max(matches, key=lambda match: risk_priority[match.risk]).risk,
        matched_rule_ids=tuple(sorted(match.rule_id for match in matches)),
        decisive_rule_ids=tuple(sorted(match.rule_id for match in decisive)),
        reason_code=selected.reason_code,
        reason=selected.reason,
    )
