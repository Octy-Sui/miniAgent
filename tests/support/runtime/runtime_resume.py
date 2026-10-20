from uuid import uuid4

from miniagent.runtime.events import (
    NewRunEvent,
    ResumeCompleted,
    ResumeStarted,
)
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.transitions import begin_resume, complete_resume


async def completed_protocol(store, execution):
    record = await store.get(execution.state.identity.run_id)
    state = begin_resume(
        record.state, attempt_id=uuid4(), source_sequence=record.last_event_sequence
    )
    session = state.recovery.session
    assert session is not None
    result = await execution.journal.start_resume(
        state,
        [
            NewRunEvent(
                run_id=record.state.identity.run_id,
                payload=ResumeStarted(
                    resume_attempt_id=session.attempt_id,
                    source_sequence=session.source_sequence,
                    business_status=session.business_status,
                    phase=session.phase_at_start,
                ),
            )
        ],
    )
    execution = RunExecution.attach(result.record, execution.journal)
    if execution.state.execution.active_operation is not None:
        await execution.interrupt_operation(unconfirmed=True)
    result = await execution.journal.complete_resume(
        complete_resume(execution.state),
        [
            NewRunEvent(
                run_id=record.state.identity.run_id,
                payload=ResumeCompleted(
                    resume_attempt_id=session.attempt_id,
                    source_sequence=session.source_sequence,
                    business_status=session.business_status,
                    phase=session.phase_at_start,
                ),
            )
        ],
    )
    return result.record
