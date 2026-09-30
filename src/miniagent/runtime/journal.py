from collections.abc import Sequence
from uuid import UUID, uuid4
from hashlib import sha256

from miniagent.runtime.context import CompactionPlan, QwenPromptTokenEstimator

from miniagent.policy import ApprovalRequest, ApprovalResolution, PolicyDecision
from miniagent.runtime.events import (
    ApprovalInvalidated,
    ApprovalRequested,
    ApprovalResolved,
    NewRunEvent,
    CompactionAttempt,
    CompactionCompleted,
)
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.state import RunState, ConversationState, ContextProgress
from miniagent.runtime.store_contracts import (
    ApprovalExpiredError,
    CreateApproval,
    InvalidateApproval,
    ResolveApproval,
    CommitResult,
    SnapshotChange,
)


class ApprovalCommitExpired(RuntimeError):
    pass


class RunJournal:
    def __init__(self, recorder: RunRecorder) -> None:
        self.recorder = recorder
        self._approval_versions: dict[UUID, int] = {}

    def prepare_compaction(self, state: RunState, conversation: ConversationState, plan: CompactionPlan, *, attempts: tuple[CompactionAttempt, ...], new_tokens: int, summary_input_tokens: int = 0, source_snapshot_id: UUID | None = None) -> tuple[RunState, NewRunEvent]:
        record = self.recorder.record
        previous = state.context.current_version
        version = (previous or 0) + 1
        snapshot_id = uuid4() if record is not None else None
        snapshot_version = record.latest_snapshot_version + 1 if record is not None else None
        sequence = record.last_event_sequence + 1 if record is not None else None
        candidate = state.model_copy(update={'conversation': conversation, 'context': ContextProgress(current_version=version)})
        content = conversation.messages[2].content
        assert content is not None
        budget = plan.budget
        event = NewRunEvent(run_id=state.identity.run_id, payload=CompactionCompleted(previous_version=previous, current_version=version, source_snapshot_version=plan.source_snapshot_version, source_snapshot_id=source_snapshot_id, source_event_sequence=plan.source_event_sequence, new_snapshot_version=snapshot_version, new_snapshot_id=snapshot_id, event_sequence=sequence, covered_messages=plan.covered_messages, covered_steps=plan.covered_steps, retained_first_step=plan.retained_first_step, summary_sha256=sha256(content.encode()).hexdigest(), provider=state.definition.agent_spec.model.provider, model=state.definition.agent_spec.model.model, context_window_tokens=budget.C, output_reserve=budget.O, safety_margin=budget.S, business_budget=budget.B, summary_reserve=budget.summary_reserve, old_input_tokens=plan.T, new_input_tokens=new_tokens, summary_input_tokens=summary_input_tokens, hard_trigger=plan.hard_trigger, attempts=attempts))
        return candidate, event

    async def compact(self, state: RunState, event: NewRunEvent) -> CommitResult | None:
        payload = event.payload
        if not isinstance(payload, CompactionCompleted):
            raise ValueError('compaction commit requires completion event')
        record = self.recorder.record
        change = None
        if record is not None:
            if payload.new_snapshot_version != record.latest_snapshot_version + 1 or payload.new_snapshot_id is None or payload.event_sequence != record.last_event_sequence + 1:
                raise ValueError('compaction reservation does not match confirmed record')
            change = SnapshotChange(snapshot_id=payload.new_snapshot_id, expected_version=record.latest_snapshot_version)
        return await self.recorder.commit(state, events=[event], snapshot=True, snapshot_change=change)

    async def create(self, state: RunState) -> None:
        await self.recorder.create(state)

    async def start_run(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
    ) -> None:
        await self.recorder.commit(state, events=events, snapshot=True)

    async def record_activity(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
    ) -> None:
        await self.recorder.commit(state, events=events)

    async def complete_step(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
    ) -> None:
        await self.recorder.commit(state, events=events, snapshot=True)

    async def open_approval(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
        *,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> None:
        result = await self.recorder.commit(
            state,
            events=events,
            approval_changes=[
                CreateApproval(
                    request=request,
                    decision=decision,
                    request_event_id=self._event_id(
                        events,
                        ApprovalRequested,
                        request.approval_id,
                    ),
                )
            ],
            snapshot=True,
        )
        self._approval_versions[request.approval_id] = (
            result.approvals[0].version if result is not None else 1
        )

    async def resolve_approval(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
        resolution: ApprovalResolution,
    ) -> None:
        try:
            result = await self.recorder.commit(
                state,
                events=events,
                approval_changes=[
                    ResolveApproval(
                        resolution=resolution,
                        expected_version=self._approval_version(
                            resolution.approval_id
                        ),
                        resolution_event_id=self._event_id(
                            events,
                            ApprovalResolved,
                            resolution.approval_id,
                        ),
                    )
                ],
            )
        except RunPersistenceError as exc:
            if not isinstance(exc.__cause__, ApprovalExpiredError):
                raise
            raise ApprovalCommitExpired(
                "Approval expired while committing its resolution"
            ) from exc
        self._approval_versions[resolution.approval_id] = (
            result.approvals[0].version if result is not None else 2
        )

    async def invalidate_approval(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
        approval_id: UUID,
    ) -> None:
        await self.recorder.commit(
            state,
            events=events,
            approval_changes=[
                InvalidateApproval(
                    approval_id=approval_id,
                    expected_version=self._approval_version(approval_id),
                    invalidated_event_id=self._event_id(
                        events,
                        ApprovalInvalidated,
                        approval_id,
                    ),
                )
            ],
        )

    async def finish_run(
        self,
        state: RunState,
        events: Sequence[NewRunEvent],
        *,
        approval_resolution: ApprovalResolution | None = None,
    ) -> None:
        approval_changes = []
        if approval_resolution is not None:
            approval_changes.append(
                ResolveApproval(
                    resolution=approval_resolution,
                    expected_version=self._approval_version(
                        approval_resolution.approval_id
                    ),
                    resolution_event_id=self._event_id(
                        events,
                        ApprovalResolved,
                        approval_resolution.approval_id,
                    ),
                )
            )
        await self.recorder.commit(
            state,
            events=events,
            approval_changes=approval_changes,
            snapshot=True,
        )

    def _approval_version(self, approval_id: UUID) -> int:
        try:
            return self._approval_versions[approval_id]
        except KeyError as exc:
            raise RuntimeError(
                f"Approval version is unavailable: {approval_id}"
            ) from exc

    @staticmethod
    def _event_id(
        events: Sequence[NewRunEvent],
        payload_type: type[ApprovalRequested | ApprovalResolved | ApprovalInvalidated],
        approval_id: UUID,
    ) -> UUID:
        matches = [
            event.event_id
            for event in events
            if isinstance(event.payload, payload_type)
            and event.payload.approval_id == approval_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one {payload_type.__name__} event for approval {approval_id}"
            )
        return matches[0]
