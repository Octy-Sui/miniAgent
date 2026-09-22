from collections.abc import Sequence
from uuid import UUID

from miniagent.policy import ApprovalRequest, ApprovalResolution, PolicyDecision
from miniagent.runtime.events import NewRunEvent, RunEventType
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.state import RunState
from miniagent.storage import (
    ApprovalExpiredError,
    CreateApproval,
    InvalidateApproval,
    ResolveApproval,
)


class ApprovalCommitExpired(RuntimeError):
    pass


class RunJournal:
    def __init__(self, recorder: RunRecorder) -> None:
        self.recorder = recorder
        self._approval_versions: dict[UUID, int] = {}

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
                    request_event_index=self._event_index(
                        events,
                        RunEventType.APPROVAL_REQUESTED,
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
                        resolution_event_index=self._event_index(
                            events,
                            RunEventType.APPROVAL_RESOLVED,
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
                    invalidated_event_index=self._event_index(
                        events,
                        RunEventType.APPROVAL_INVALIDATED,
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
                    resolution_event_index=self._event_index(
                        events,
                        RunEventType.APPROVAL_RESOLVED,
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
    def _event_index(
        events: Sequence[NewRunEvent],
        event_type: RunEventType,
        approval_id: UUID,
    ) -> int:
        matches = [
            index
            for index, event in enumerate(events)
            if event.type is event_type
            and event.payload.get("approval_id") == str(approval_id)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one {event_type.value} event for approval {approval_id}"
            )
        return matches[0]
