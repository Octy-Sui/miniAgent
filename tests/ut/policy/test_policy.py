from typing import Literal
from uuid import UUID

import pytest

from miniagent.policy import (
    DefaultPolicyEngine,
    PolicyContext,
    PolicyEffect,
    UnknownOperation,
    build_policy_request,
)
from miniagent.tools.contracts import ToolDefinition
from tests.support.policy.policy import (
    RecordingShell,
    make_agent,
)


@pytest.mark.parametrize(
    ("path", "workspace_kind", "expected_effect", "expected_rule"),
    [
        ("../secret", "local", PolicyEffect.DENY, "path.outside_workspace"),
        ("src/main.py", "local", PolicyEffect.ALLOW, "read.workspace"),
        ("src/main.py", "docker", PolicyEffect.ALLOW, "read.workspace"),
    ],
)
def test_default_read_policy(
    path: str,
    workspace_kind: Literal["local", "docker"],
    expected_effect: PolicyEffect,
    expected_rule: str,
) -> None:
    agent = make_agent()
    definition = ToolDefinition(
        name="read_file",
        description="Read a file.",
        input_schema={"type": "object"},
    )
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="read",
        tool_name="read_file",
        arguments={"path": path},
        agent=agent,
        definition=definition,
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind=workspace_kind,
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert evaluation.effect is expected_effect
    assert evaluation.decisive_rule_ids == (expected_rule,)


@pytest.mark.parametrize(
    ("workspace_kind", "expected_effect", "expected_rule"),
    [
        ("local", PolicyEffect.APPROVAL, "shell.local_human_review"),
        ("docker", PolicyEffect.ALLOW, "shell.docker_workspace"),
    ],
)
def test_default_shell_policy(
    workspace_kind: Literal["local", "docker"],
    expected_effect: PolicyEffect,
    expected_rule: str,
) -> None:
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="shell",
        tool_name="shell",
        arguments={"command": "pwd"},
        agent=make_agent(),
        definition=RecordingShell().definition,
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind=workspace_kind,
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert evaluation.effect is expected_effect
    assert evaluation.decisive_rule_ids == (expected_rule,)


def test_unknown_tool_maps_to_typed_operation_and_is_denied() -> None:
    request = build_policy_request(
        run_id=UUID("d8d44120-44b0-4a69-ac9e-781ae5e63a4d"),
        step_number=1,
        call_index=0,
        tool_call_id="unknown",
        tool_name="unknown_tool",
        arguments={"risk": "low"},
        agent=make_agent(),
        definition=ToolDefinition(
            name="unknown_tool",
            description="Unknown.",
            input_schema={"type": "object"},
        ),
        context=PolicyContext(
            workspace_id="workspace",
            workspace_kind="docker",
            revision="revision",
        ),
    )

    evaluation = DefaultPolicyEngine().evaluate(request)

    assert isinstance(request.operation, UnknownOperation)
    assert evaluation.effect is PolicyEffect.DENY
    assert evaluation.reason_code == "NO_ALLOWING_POLICY"
    assert DefaultPolicyEngine().revision == "default-v4"
