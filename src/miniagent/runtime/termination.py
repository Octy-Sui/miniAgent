# File   : termination.py
# Created: 2026-09-10 16:59:14
# Author : octy Sui

from dataclasses import dataclass

from miniagent.evaluation import CandidateOutput
from miniagent.models.protocol import FinishReason, ModelResponse
from miniagent.runtime.result import RunError, TerminationReason
from miniagent.runtime.state import RunStatus
from miniagent.tools.contracts import ToolResult, ToolStatus


@dataclass(frozen=True)
class ModelDecision:
    target: RunStatus
    reason: TerminationReason | None = None
    candidate_output: CandidateOutput | None = None
    error: RunError | None = None


def evaluate_model_response(response: ModelResponse) -> ModelDecision | None:
    """
    Task进入评估条件: finish_reason == STOP and tool_calls 为空 and content 非空;
    Task仍在执行条件: finish_reason == TOOL_CALL and tool_calls 非空;
    Task失败条件: 其他场景均为失败
    """
    if response.error is not None:
        # Task失败, 模型请求失败
        return ModelDecision(
            target=RunStatus.FAILED,
            reason=TerminationReason.MODEL_FAILED,
            error=RunError(
                code=response.error.code,
                message=response.error.message,
                retryable=response.error.retryable,
            ),
        )

    if response.finish_reason is FinishReason.STOP:
        if response.message.tool_calls:
            return _invalid_response(
                "MODEL_STOPPED_WITH_TOOL_CALLS",
                "Model stopped while returning tool calls.",
            )
        if response.message.content is None or not response.message.content.strip():
            return _invalid_response(
                "MODEL_STOPPED_WITHOUT_CONTENT",
                "Model stopped without a final response.",
            )
        return ModelDecision(
            target=RunStatus.EVALUATING,
            candidate_output=CandidateOutput(text=response.message.content),
        )

    if response.finish_reason is FinishReason.TOOL_CALL:
        # Task仍在执行, FinishReason原因TOOL_CALL, tool_calls字段不为空, 不满足退出条件
        if response.message.tool_calls:
            call_ids = [call.id for call in response.message.tool_calls]
            if len(call_ids) != len(set(call_ids)):
                return _invalid_response(
                    "MODEL_DUPLICATE_TOOL_CALL_ID",
                    "Model returned duplicate tool call IDs.",
                )
            return None
        # Task失败, 模型返回FinishReason原因TOOL_CALL, 但是tool_calls字段为空
        return _invalid_response(
            "MODEL_TOOL_CALLS_MISSING",
            "Model reported tool calls but returned none.",
        )

    # 其他非正常返回FinishReason
    return ModelDecision(
        target=RunStatus.FAILED,
        reason=TerminationReason.MODEL_FAILED,
        error=RunError(
            code="MODEL_ABNORMAL_FINISH",
            message=f"Model ended with finish reason: {response.finish_reason}",
        ),
    )


def tool_step_failed(results: list[ToolResult]) -> bool:
    return bool(results) and all(
        result.status is not ToolStatus.SUCCESS for result in results
    )


def _invalid_response(code: str, message: str) -> ModelDecision:
    return ModelDecision(
        target=RunStatus.FAILED,
        reason=TerminationReason.MODEL_FAILED,
        error=RunError(code=code, message=message),
    )
