import secrets
from dataclasses import dataclass
from uuid import UUID

from miniagent.runtime.events import EventPayload, NewRunEvent


def _span_id() -> str:
    return secrets.token_hex(8)


@dataclass(frozen=True, slots=True)
class RunScope:
    span_id: str


@dataclass(frozen=True, slots=True)
class ModelScope:
    span_id: str


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    span_id: str


@dataclass(frozen=True, slots=True)
class ToolExecutionScope:
    span_id: str


@dataclass(frozen=True, slots=True)
class EvaluationScope:
    span_id: str


@dataclass(frozen=True, slots=True)
class ToolCorrelation:
    call_index: int
    tool_call_id: str
    tool_name: str


@dataclass(slots=True)
class ToolScope:
    correlation: ToolCorrelation
    span_id: str
    approval: ApprovalScope | None = None
    execution: ToolExecutionScope | None = None


@dataclass(slots=True)
class StepScope:
    number: int
    span_id: str
    model: ModelScope | None = None
    tool: ToolScope | None = None


@dataclass(slots=True)
class RunTraceContext:
    run_id: UUID
    run: RunScope
    step: StepScope | None = None
    evaluation: EvaluationScope | None = None

    @classmethod
    def create(cls, run_id: UUID) -> "RunTraceContext":
        return cls(run_id=run_id, run=RunScope(span_id=_span_id()))

    @property
    def has_step(self) -> bool:
        return self.step is not None

    @property
    def has_tool(self) -> bool:
        return self._active_tool() is not None

    @property
    def has_model(self) -> bool:
        return self.step is not None and self.step.model is not None

    @property
    def has_approval(self) -> bool:
        tool = self._active_tool()
        return tool is not None and tool.approval is not None

    @property
    def has_tool_execution(self) -> bool:
        tool = self._active_tool()
        return tool is not None and tool.execution is not None

    @property
    def has_evaluation(self) -> bool:
        return self.evaluation is not None

    @property
    def tool_correlation(self) -> ToolCorrelation:
        return self._require_tool().correlation

    def run_event(self, payload: EventPayload) -> NewRunEvent:
        return self._event(payload, span_id=self.run.span_id, include_step=False)

    def begin_evaluation(self) -> None:
        if self.step is not None or self.evaluation is not None:
            raise RuntimeError("Cannot begin evaluation while another scope is active")
        self.evaluation = EvaluationScope(span_id=_span_id())

    def evaluation_event(self, payload: EventPayload) -> NewRunEvent:
        if self.evaluation is None:
            raise RuntimeError("Evaluation event requires an active evaluation")
        return self._event(
            payload,
            span_id=self.evaluation.span_id,
            parent_span_id=self.run.span_id,
            include_step=False,
        )

    def end_evaluation(self) -> None:
        if self.evaluation is None:
            raise RuntimeError("Operation requires an active evaluation")
        self.evaluation = None

    def begin_step(self, number: int) -> None:
        if self.step is not None:
            raise RuntimeError("Cannot begin a step while another step is active")
        self.step = StepScope(number=number, span_id=_span_id())

    def step_event(self, payload: EventPayload) -> NewRunEvent:
        step = self._require_step()
        return self._event(
            payload,
            span_id=step.span_id,
            parent_span_id=self.run.span_id,
        )

    def end_step(self) -> None:
        step = self._require_step()
        if step.model is not None or step.tool is not None:
            raise RuntimeError("Cannot end a step with an active child scope")
        self.step = None

    def begin_model(self) -> None:
        step = self._require_step()
        if step.model is not None:
            raise RuntimeError("Model request is already active")
        step.model = ModelScope(span_id=_span_id())

    def model_event(self, payload: EventPayload) -> NewRunEvent:
        model = self._require_model()
        return self._event(
            payload,
            span_id=model.span_id,
            parent_span_id=self._require_step().span_id,
        )

    def end_model(self) -> None:
        if self._require_step().model is None:
            raise RuntimeError("Operation requires an active model request")
        self._require_step().model = None

    def begin_tool(self, correlation: ToolCorrelation) -> None:
        step = self._require_step()
        if step.tool is not None:
            raise RuntimeError("Cannot begin a tool call while another is active")
        step.tool = ToolScope(correlation=correlation, span_id=_span_id())

    def tool_event(self, payload: EventPayload) -> NewRunEvent:
        tool = self._require_tool()
        return self._event(
            payload,
            span_id=tool.span_id,
            parent_span_id=self._require_step().span_id,
        )

    def end_tool(self) -> None:
        tool = self._require_tool()
        if tool.approval is not None or tool.execution is not None:
            raise RuntimeError("Cannot end a tool call with an active child scope")
        self._require_step().tool = None

    def begin_tool_execution(self) -> None:
        tool = self._require_tool()
        if tool.execution is not None:
            raise RuntimeError("Tool execution is already active")
        tool.execution = ToolExecutionScope(span_id=_span_id())

    def tool_execution_event(self, payload: EventPayload) -> NewRunEvent:
        execution = self._require_tool_execution()
        return self._event(
            payload,
            span_id=execution.span_id,
            parent_span_id=self._require_tool().span_id,
        )

    def end_tool_execution(self) -> None:
        if self._require_tool().execution is None:
            raise RuntimeError("Operation requires active tool execution")
        self._require_tool().execution = None

    def begin_approval(self) -> None:
        tool = self._require_tool()
        if tool.approval is not None:
            raise RuntimeError("Approval is already active")
        tool.approval = ApprovalScope(span_id=_span_id())

    def approval_event(self, payload: EventPayload) -> NewRunEvent:
        approval = self._require_approval()
        return self._event(
            payload,
            span_id=approval.span_id,
            parent_span_id=self._require_tool().span_id,
        )

    def end_approval(self) -> None:
        if self._require_tool().approval is None:
            raise RuntimeError("Operation requires an active approval")
        self._require_tool().approval = None

    def _event(
        self,
        payload: EventPayload,
        *,
        span_id: str,
        parent_span_id: str | None = None,
        include_step: bool = True,
    ) -> NewRunEvent:
        return NewRunEvent(
            run_id=self.run_id,
            payload=payload,
            step_number=self._require_step().number if include_step else None,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )

    def _active_tool(self) -> ToolScope | None:
        return self.step.tool if self.step is not None else None

    def _require_step(self) -> StepScope:
        if self.step is None:
            raise RuntimeError("Operation requires an active step")
        return self.step

    def _require_model(self) -> ModelScope:
        model = self._require_step().model
        if model is None:
            raise RuntimeError("Operation requires an active model request")
        return model

    def _require_tool(self) -> ToolScope:
        tool = self._active_tool()
        if tool is None:
            raise RuntimeError("Operation requires an active tool call")
        return tool

    def _require_tool_execution(self) -> ToolExecutionScope:
        execution = self._require_tool().execution
        if execution is None:
            raise RuntimeError("Operation requires active tool execution")
        return execution

    def _require_approval(self) -> ApprovalScope:
        approval = self._require_tool().approval
        if approval is None:
            raise RuntimeError("Approval event requires an active approval")
        return approval
