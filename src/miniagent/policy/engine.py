# File   : engine.py
# Created: 2026-09-17 17:09:25
# Author : octy Sui

import hashlib
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Protocol
from uuid import UUID, uuid4

import orjson
from pydantic import JsonValue

from miniagent.agents import AgentSpec
from miniagent.models.tool import ToolDefinition
from miniagent.policy.models import (
    PolicyContext,
    PolicyDecision,
    PolicyEffect,
    PolicyEvaluation,
    PolicyRequest,
    RiskLevel,
)


class PolicyEngine(Protocol):
    @property
    def revision(self) -> str: ...

    def evaluate(self, request: PolicyRequest) -> PolicyEvaluation: ...


class DefaultPolicyEngine:
    @property
    def revision(self) -> str:
        return "default-v2"

    def evaluate(self, request: PolicyRequest) -> PolicyEvaluation:
        matches: list[tuple[str, PolicyEffect, RiskLevel, str, str]] = []
        if request.tool_name == "read_file":
            path = request.arguments.get("path")
            if not isinstance(path, str):
                matches.append(
                    (
                        "read.invalid_path",
                        PolicyEffect.DENY,
                        RiskLevel.HIGH,
                        "INVALID_READ_PATH",
                        "The file path is not a string.",
                    )
                )
            else:
                relative = PurePosixPath(path)
                if relative.is_absolute() or ".." in relative.parts:
                    matches.append(
                        (
                            "path.outside_workspace",
                            PolicyEffect.DENY,
                            RiskLevel.HIGH,
                            "PATH_OUTSIDE_WORKSPACE",
                            "The requested path escapes the workspace.",
                        )
                    )
                else:
                    matches.append(
                        (
                            "read.workspace",
                            PolicyEffect.ALLOW,
                            RiskLevel.LOW,
                            "WORKSPACE_READ_ALLOWED",
                            "Reading a relative workspace path is allowed.",
                        )
                    )
        elif request.tool_name == "shell":
            if request.workspace_kind == "docker":
                matches.append(
                    (
                        "shell.docker_workspace",
                        PolicyEffect.ALLOW,
                        RiskLevel.MEDIUM,
                        "DOCKER_SHELL_ALLOWED",
                        "Shell execution in the isolated Docker workspace is allowed.",
                    )
                )
            else:
                matches.append(
                    (
                        "shell.local_human_review",
                        PolicyEffect.APPROVAL,
                        RiskLevel.UNKNOWN,
                        "SHELL_REQUIRES_APPROVAL",
                        "Shell execution in a local workspace requires human approval.",
                    )
                )
        else:
            matches.append(
                (
                    "default.deny",
                    PolicyEffect.DENY,
                    RiskLevel.UNKNOWN,
                    "NO_ALLOWING_POLICY",
                    "No policy allows this tool call.",
                )
            )

        priority = {
            PolicyEffect.ALLOW: 0,
            PolicyEffect.APPROVAL: 1,
            PolicyEffect.DENY: 2,
        }
        effect = max((match[1] for match in matches), key=priority.__getitem__)
        decisive = [match for match in matches if match[1] is effect]
        risk_priority = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.UNKNOWN: 3,
        }
        return PolicyEvaluation(
            effect=effect,
            risk=max((match[2] for match in matches), key=risk_priority.__getitem__),
            matched_rule_ids=tuple(sorted(match[0] for match in matches)),
            decisive_rule_ids=tuple(sorted(match[0] for match in decisive)),
            reason_code=decisive[0][3],
            reason=decisive[0][4],
        )


def digest_json(value: Any) -> str:
    _reject_non_finite(value)
    encoded = orjson.dumps(value, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(encoded).hexdigest()


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Policy digests do not accept non-finite numbers")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def build_policy_request(
    *,
    run_id: UUID,
    step_number: int,
    call_index: int,
    tool_call_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    agent: AgentSpec,
    definition: ToolDefinition,
    context: PolicyContext,
    tool_policy_metadata: Mapping[str, JsonValue] | None = None,
) -> PolicyRequest:
    json_arguments: dict[str, JsonValue] = orjson.loads(orjson.dumps(arguments))
    return PolicyRequest(
        run_id=run_id,
        step_number=step_number,
        call_index=call_index,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=json_arguments,
        agent_spec_digest=digest_json(agent.model_dump(mode="json")),
        tool_contract_digest=digest_json(
            {
                "definition": definition.model_dump(mode="json"),
                "policy_metadata": dict(tool_policy_metadata or {}),
            }
        ),
        workspace_id=context.workspace_id,
        workspace_kind=context.workspace_kind,
        execution_context_digest=digest_json(context.model_dump(mode="json")),
    )


def make_policy_decision(
    request: PolicyRequest,
    engine: PolicyEngine,
    *,
    decided_at: datetime,
) -> PolicyDecision:
    evaluation = engine.evaluate(request)
    return PolicyDecision(
        decision_id=uuid4(),
        request=request,
        request_digest=digest_json(request.model_dump(mode="json")),
        policy_revision=engine.revision,
        effect=evaluation.effect,
        risk=evaluation.risk,
        matched_rule_ids=evaluation.matched_rule_ids,
        decisive_rule_ids=evaluation.decisive_rule_ids,
        reason_code=evaluation.reason_code,
        reason=evaluation.reason,
        input_summary=summarize_input(request),
        decided_at=decided_at,
    )


def summarize_input(request: PolicyRequest) -> str:
    if request.tool_name == "read_file":
        return f"read_file path={request.arguments.get('path')!r}"[:2048]
    if request.tool_name == "shell":
        return f"shell command={request.arguments.get('command')!r}"[:2048]
    return f"{request.tool_name} with {len(request.arguments)} argument(s)"
