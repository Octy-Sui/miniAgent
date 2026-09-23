from miniagent.evaluation.contracts import EvaluationRequest, EvaluationResult


class PassThroughEvaluator:
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        return EvaluationResult(passed=True, output=request.candidate.text)
