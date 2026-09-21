import secrets
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from miniagent.runtime.events import NewRunEvent, RunEventType
from miniagent.runtime.state import RunStatus, TerminationReason


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


@dataclass(slots=True)
class ToolScope:
    call_index: int
    call_id: str
    name: str
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

    @classmethod
    def create(cls, run_id: UUID) -> "RunTraceContext":
        return cls(run_id=run_id, run=RunScope(span_id=_span_id()))

    def run_started(self) -> NewRunEvent:
        return self._event(RunEventType.RUN_STARTED, span_id=self.run.span_id)

    def begin_step(
        self,
        *,
        number: int,
        provider: str,
        model: str,
    ) -> list[NewRunEvent]:
        if self.step is not None:
            raise RuntimeError("Cannot begin a step while another step is active")
        self.step = StepScope(
            number=number,
            span_id=_span_id(),
            model=ModelScope(span_id=_span_id()),
        )
        return [
            self._event(RunEventType.STEP_STARTED, step=True),
            self._event(
                RunEventType.MODEL_REQUESTED,
                payload={"provider": provider, "model": model},
                span_id=self._require_model().span_id,
                parent_span_id=self.step.span_id,
            ),
        ]

    def model_responded(
        self,
        *,
        finish_reason: str,
        tool_call_count: int,
    ) -> NewRunEvent:
        event = self._event(
            RunEventType.MODEL_RESPONDED,
            payload={
                "finish_reason": finish_reason,
                "tool_call_count": tool_call_count,
            },
            span_id=self._require_model().span_id,
            parent_span_id=self._require_step().span_id,
        )
        self._require_step().model = None
        return event

    def complete_step(self, payload: dict[str, JsonValue]) -> NewRunEvent:
        event = self._event(
            RunEventType.STEP_COMPLETED,
            payload=payload,
            step=True,
        )
        self.step = None
        return event

    def begin_tool(
        self,
        *,
        call_index: int,
        tool_call_id: str,
        tool_name: str,
    ) -> NewRunEvent:
        step = self._require_step()
        if step.tool is not None:
            raise RuntimeError("Cannot begin a tool call while another is active")
        step.tool = ToolScope(
            call_index=call_index,
            call_id=tool_call_id,
            name=tool_name,
            span_id=_span_id(),
        )
        return self._tool_event(RunEventType.TOOL_START)

    def begin_tool_execution(self) -> NewRunEvent:
        tool = self._require_tool()
        if tool.execution is not None:
            raise RuntimeError("Tool execution is already active")
        tool.execution = ToolExecutionScope(span_id=_span_id())
        return self._tool_child_event(
            RunEventType.TOOL_EXECUTION_START,
            span_id=tool.execution.span_id,
        )

    def complete_tool(
        self,
        payload: dict[str, JsonValue],
    ) -> list[NewRunEvent]:
        tool = self._require_tool()
        events: list[NewRunEvent] = []
        if tool.execution is not None:
            events.append(
                self._tool_child_event(
                    RunEventType.TOOL_EXECUTION_END,
                    span_id=tool.execution.span_id,
                    payload=payload,
                )
            )
        events.append(self._tool_event(RunEventType.TOOL_END, payload=payload))
        self._require_step().tool = None
        return events

    def begin_approval(self) -> None:
        tool = self._require_tool()
        if tool.approval is not None:
            raise RuntimeError("Approval is already active")
        tool.approval = ApprovalScope(span_id=_span_id())

    def approval_requested(
        self,
        *,
        approval_id: str,
        decision_id: str,
    ) -> NewRunEvent:
        return self._approval_event(
            RunEventType.APPROVAL_REQUESTED,
            payload={"approval_id": approval_id, "decision_id": decision_id},
        )

    def approval_resolved(
        self,
        *,
        approval_id: str,
        outcome: str,
    ) -> NewRunEvent:
        return self._approval_event(
            RunEventType.APPROVAL_RESOLVED,
            payload={"approval_id": approval_id, "outcome": outcome},
        )

    def complete_approval(self) -> None:
        if self._require_tool().approval is None:
            raise RuntimeError("Operation requires an active approval")
        self._require_tool().approval = None

    def approval_invalidated(
        self,
        *,
        approval_id: str,
        reason: str,
    ) -> NewRunEvent:
        return self._event(
            RunEventType.APPROVAL_INVALIDATED,
            payload={"approval_id": approval_id, "reason": reason},
        )

    def evaluation_completed(self, *, passed: bool) -> NewRunEvent:
        return self._event(
            RunEventType.EVALUATION_COMPLETED,
            payload={"passed": passed},
        )

    def finish(
        self,
        *,
        status: RunStatus,
        reason: TerminationReason,
    ) -> list[NewRunEvent]:
        events: list[NewRunEvent] = []
        if self._active_tool() is not None:
            payload: dict[str, JsonValue] = {
                "status": status.value,
                "reason": reason.value,
            }
            tool = self._require_tool()
            if tool.execution is not None:
                events.append(
                    self._tool_child_event(
                        RunEventType.TOOL_EXECUTION_END,
                        span_id=tool.execution.span_id,
                        payload=payload,
                    )
                )
            events.append(self._tool_event(RunEventType.TOOL_END, payload=payload))
        if self.step is not None:
            events.append(
                self._event(
                    RunEventType.STEP_COMPLETED,
                    payload={"failed": status is not RunStatus.SUCCEEDED},
                    step=True,
                )
            )
        events.append(
            self._event(
                RunEventType.RUN_FINISHED,
                payload={"status": status.value, "reason": reason.value},
                span_id=self.run.span_id,
            )
        )
        self.step = None
        return events

    def _event(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
        step: bool = False,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> NewRunEvent:
        in_step = self.step is not None and event_type is not RunEventType.RUN_FINISHED
        return NewRunEvent(
            run_id=self.run_id,
            type=event_type,
            payload=payload or {},
            step_number=self._require_step().number if step or in_step else None,
            span_id=self._require_step().span_id if step else span_id,
            parent_span_id=(
                self.run.span_id if step and parent_span_id is None else parent_span_id
            ),
        )

    def _tool_event(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        tool = self._require_tool()
        return self._event(
            event_type,
            payload={
                "call_index": tool.call_index,
                "tool_call_id": tool.call_id,
                "tool_name": tool.name,
                **(payload or {}),
            },
            span_id=tool.span_id,
            parent_span_id=self._require_step().span_id,
        )

    def _tool_child_event(
        self,
        event_type: RunEventType,
        *,
        span_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> NewRunEvent:
        event = self._tool_event(event_type, payload=payload)
        return event.model_copy(
            update={"span_id": span_id, "parent_span_id": self._require_tool().span_id}
        )

    def _approval_event(
        self,
        event_type: RunEventType,
        *,
        payload: dict[str, JsonValue],
    ) -> NewRunEvent:
        approval = self._require_tool().approval
        if approval is None:
            raise RuntimeError("Approval event requires an active approval")
        return self._tool_child_event(
            event_type,
            span_id=approval.span_id,
            payload=payload,
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
