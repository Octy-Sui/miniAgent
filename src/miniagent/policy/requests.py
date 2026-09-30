import hashlib
import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import orjson
from pydantic import JsonValue

from miniagent.agents import AgentSpec
from miniagent.policy.contracts import (
    Operation,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
    ShellExecution,
    UnknownOperation,
    WorkspaceRead,
)
from miniagent.tools.contracts import ToolDefinition


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


def operation_for_tool(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> Operation:
    if tool_name == "read_file":
        path = arguments.get("path")
        if isinstance(path, str):
            return WorkspaceRead(path=path)
        return UnknownOperation(tool_name=tool_name, reason="invalid_read_path")
    if tool_name == "shell":
        command = arguments.get("command")
        if isinstance(command, str):
            return ShellExecution(command=command)
    return UnknownOperation(tool_name=tool_name)


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
    tool_contract_metadata: Mapping[str, JsonValue] | None = None,
) -> PolicyRequest:
    json_arguments: dict[str, JsonValue] = orjson.loads(orjson.dumps(arguments))
    return PolicyRequest(
        run_id=run_id,
        step_number=step_number,
        call_index=call_index,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=json_arguments,
        operation=operation_for_tool(tool_name, arguments),
        agent_spec_digest=digest_json(agent.model_dump(mode="json")),
        tool_contract_digest=digest_json(
            dict(
                tool_contract_metadata
                or {
                    "name": definition.name,
                    "version": "1",
                    "input_schema": definition.input_schema,
                    "execution_options": {},
                }
            )
        ),
        workspace_id=context.workspace_id,
        workspace_kind=context.workspace_kind,
        execution_context_digest=digest_json(context.model_dump(mode="json")),
    )


def summarize_input(request: PolicyRequest) -> str:
    operation = request.operation
    if isinstance(operation, WorkspaceRead):
        return f"read_file path={operation.path!r}"[:2048]
    if isinstance(operation, ShellExecution):
        return f"shell command={operation.command!r}"[:2048]
    return f"{operation.tool_name} with {len(request.arguments)} argument(s)"


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
