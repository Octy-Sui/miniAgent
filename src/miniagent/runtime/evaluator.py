from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.environments import Command, WorkspaceSpec
from miniagent.models.artifact import ArtifactRef
from miniagent.runtime.state import RunError


class EvaluationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace: WorkspaceSpec
    target_commands: list[Command] = Field(min_length=1)
    regression_commands: list[Command] = Field(default_factory=list)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    task: str
    candidate_output: str
    patch: ArtifactRef | None = None
    plan: EvaluationPlan | None = None

    @model_validator(mode="after")
    def validate_plan_run_id(self) -> "EvaluationRequest":
        if self.plan is not None and self.plan.workspace.run_id != self.run_id:
            raise ValueError("Evaluation plan must belong to the evaluated run")
        return self


class EvaluationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    passed: bool
    summary: str | None = None
    artifact: ArtifactRef | None = None
    duration_ms: int | None = Field(default=None, ge=0)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    output: str | None = None
    error: RunError | None = None
    checks: list[EvaluationCheck] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_checks(self) -> "EvaluationResult":
        if self.passed and any(not check.passed for check in self.checks):
            raise ValueError("Passed evaluation cannot contain a failed check")
        return self


class Evaluator(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


class PassThroughEvaluator(Evaluator):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(passed=True, output=request.candidate_output)
