from abc import ABC, abstractmethod
from uuid import UUID

from pydantic import BaseModel

from miniagent.runtime.state import RunError


class EvaluationRequest(BaseModel):
    run_id: UUID
    task: str
    candidate_output: str


class EvaluationResult(BaseModel):
    passed: bool
    output: str | None = None
    error: RunError | None = None


class Evaluator(ABC):
    @abstractmethod
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


class PassThroughEvaluator(Evaluator):
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(passed=True, output=request.candidate_output)
