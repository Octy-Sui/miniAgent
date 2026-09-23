from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.artifacts.contracts import ArtifactRef
from miniagent.environments.contracts import Command


class CandidateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    patch: ArtifactRef | None = None


class EvaluationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_commands: list[Command] = Field(min_length=1)
    regression_commands: list[Command] = Field(default_factory=list)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    task: str
    candidate: CandidateOutput
    plan: EvaluationPlan | None = None


class EvaluationError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool = False


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
    error: EvaluationError | None = None
    checks: list[EvaluationCheck] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_checks(self) -> "EvaluationResult":
        if self.passed and any(not check.passed for check in self.checks):
            raise ValueError("Passed evaluation cannot contain a failed check")
        return self


class Evaluator(Protocol):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...
