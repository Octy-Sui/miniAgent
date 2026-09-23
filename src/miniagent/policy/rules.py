from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from miniagent.policy.contracts import (
    Operation,
    PolicyContext,
    PolicyEffect,
    RiskLevel,
    ShellExecution,
    UnknownOperation,
    WorkspaceRead,
)


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule_id: str
    effect: PolicyEffect
    risk: RiskLevel
    reason_code: str
    reason: str


class PolicyRule(Protocol):
    def evaluate(
        self,
        operation: Operation,
        context: PolicyContext,
    ) -> RuleMatch | None: ...


class WorkspaceReadRule:
    def evaluate(
        self,
        operation: Operation,
        context: PolicyContext,
    ) -> RuleMatch | None:
        if not isinstance(operation, WorkspaceRead):
            return None
        relative = PurePosixPath(operation.path)
        if relative.is_absolute() or ".." in relative.parts:
            return RuleMatch(
                "path.outside_workspace",
                PolicyEffect.DENY,
                RiskLevel.HIGH,
                "PATH_OUTSIDE_WORKSPACE",
                "The requested path escapes the workspace.",
            )
        return RuleMatch(
            "read.workspace",
            PolicyEffect.ALLOW,
            RiskLevel.LOW,
            "WORKSPACE_READ_ALLOWED",
            "Reading a relative workspace path is allowed.",
        )


class ShellRule:
    def evaluate(
        self,
        operation: Operation,
        context: PolicyContext,
    ) -> RuleMatch | None:
        if not isinstance(operation, ShellExecution):
            return None
        if context.workspace_kind == "docker":
            return RuleMatch(
                "shell.docker_workspace",
                PolicyEffect.ALLOW,
                RiskLevel.MEDIUM,
                "DOCKER_SHELL_ALLOWED",
                "Shell execution in the isolated Docker workspace is allowed.",
            )
        return RuleMatch(
            "shell.local_human_review",
            PolicyEffect.APPROVAL,
            RiskLevel.UNKNOWN,
            "SHELL_REQUIRES_APPROVAL",
            "Shell execution in a local workspace requires human approval.",
        )


class UnknownOperationRule:
    def evaluate(
        self,
        operation: Operation,
        context: PolicyContext,
    ) -> RuleMatch | None:
        if not isinstance(operation, UnknownOperation):
            return None
        if operation.reason == "invalid_read_path":
            return RuleMatch(
                "read.invalid_path",
                PolicyEffect.DENY,
                RiskLevel.HIGH,
                "INVALID_READ_PATH",
                "The file path is not a string.",
            )
        return RuleMatch(
            "default.deny",
            PolicyEffect.DENY,
            RiskLevel.UNKNOWN,
            "NO_ALLOWING_POLICY",
            "No policy allows this tool call.",
        )


DEFAULT_RULES: tuple[PolicyRule, ...] = (
    WorkspaceReadRule(),
    ShellRule(),
    UnknownOperationRule(),
)
