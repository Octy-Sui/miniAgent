from pathlib import Path

from miniagent.storage import SQLiteRunStore


async def make_database(path: Path) -> Path:
    store = SQLiteRunStore(path)
    await store.initialize()
    return path


from datetime import UTC, datetime
from uuid import UUID

from miniagent.agents import AgentSpec
from miniagent.models.config import ModelTarget
from miniagent.runtime import RuntimeConfig, create_run_state


async def make_run(
    database: Path,
    number: int,
    *,
    task: str = "visible task",
    created_at: datetime | None = None,
) -> UUID:
    run_id = UUID(int=number)
    state = create_run_state(
        run_id=run_id,
        task=task,
        agent_spec=AgentSpec(
            name="dashboard-test",
            system_prompt="test",
            model=ModelTarget(provider="test", model="test-model"),
        ),
        config=RuntimeConfig(),
        created_at=created_at or datetime(2026, 10, 19, tzinfo=UTC),
    )
    await SQLiteRunStore(database).create(state)
    return run_id


from decimal import Decimal
from hashlib import sha256
from importlib.resources import files

from jinja2 import Environment, StrictUndefined

from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.reads import (
    CompactionRead,
    RunEnvelope,
    StoredEvent,
    StoredRun,
    StoredSnapshot,
)
from miniagent.metering.usage import RequestCost
from miniagent.models.messages import AssistantMessage, SystemMessage, UserMessage
from miniagent.runtime.context import CompactionSummary
from miniagent.runtime.events import (
    CompactionAttempt,
    CompactionAttemptRecorded,
    CompactionCompleted,
    CompactionRequested,
    EventPayload,
    RunEvent,
)
from miniagent.runtime.snapshots import RunSnapshot
from miniagent.runtime.state import ContextProgress, ConversationState, StepRange
from miniagent.runtime.store_contracts import RunRecord


def memory_example() -> tuple[StoredRun, PageBoundary, CompactionRead]:
    run_id = UUID(int=1)
    now = datetime(2026, 10, 19, tzinfo=UTC)
    base = create_run_state(
        run_id=run_id,
        task="task",
        agent_spec=AgentSpec(
            name="test",
            system_prompt="fixed",
            model=ModelTarget(provider="test", model="test"),
        ),
        config=RuntimeConfig(),
        created_at=now,
    )
    template = Environment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    ).from_string(
        files("miniagent.runtime").joinpath("templates/compaction_v1.j2").read_text()
    )
    summaries = [
        CompactionSummary(
            current_goal=f"goal {n}",
            user_constraints=["visible constraint"],
            verified_evidence=[],
            workspace_changes=["change"],
            rejected_attempts=[],
            uncertainties=["uncertain"],
            open_items=["finish"],
            next_action="finish",
        )
        for n in (1, 2)
    ]
    rendered = [
        template.render(summary_json=summary.model_dump_json()) for summary in summaries
    ]
    prefix = (SystemMessage(content="fixed"), UserMessage(content="task"))
    first = AssistantMessage(content="first evidence")
    retained = AssistantMessage(content="retained evidence")
    last = AssistantMessage(content="last retained")
    conversations = [
        ConversationState(
            messages=(*prefix, first, retained),
            completed_steps=(
                StepRange(step_number=1, message_start=2, message_end=3),
                StepRange(step_number=2, message_start=3, message_end=4),
            ),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[0]), retained),
            completed_steps=(StepRange(step_number=2, message_start=3, message_end=4),),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[0]), retained, last),
            completed_steps=(
                StepRange(step_number=2, message_start=3, message_end=4),
                StepRange(step_number=3, message_start=4, message_end=5),
            ),
        ),
        ConversationState(
            messages=(*prefix, AssistantMessage(content=rendered[1]), last),
            completed_steps=(StepRange(step_number=3, message_start=3, message_end=4),),
        ),
    ]
    states = [
        base.model_copy(
            update={
                "conversation": conversation,
                "context": ContextProgress(current_version=version),
            }
        )
        for conversation, version in zip(conversations, (None, 1, 1, 2), strict=True)
    ]
    snapshots = {
        n: StoredSnapshot(
            UUID(int=100 + n),
            run_id,
            n,
            sequence,
            RunSnapshot(
                run_id=run_id,
                event_sequence=sequence,
                state=state,
                snapshot_id=UUID(int=100 + n),
                version=n,
                created_at=now,
            ),
            (),
        )
        for n, (state, sequence) in enumerate(
            zip(states, (20, 40, 80, 100), strict=True), 1
        )
    }

    def stored(payload: EventPayload, sequence: int) -> StoredEvent:
        event = RunEvent(
            run_id=run_id,
            event_id=UUID(int=1000 + sequence),
            payload=payload,
            sequence=sequence,
            occurred_at=now,
        )
        return StoredEvent(
            event.event_id,
            run_id,
            sequence,
            str(event.type),
            now,
            None,
            None,
            None,
            event,
            (),
        )

    def attempt(n: int, cost: str) -> CompactionAttempt:
        price = Decimal(cost)
        return CompactionAttempt(
            attempt=n,
            request_cost=RequestCost(
                provider="test",
                model="test",
                currency="USD",
                input_cost=price,
                total_cost=price,
            ),
        )

    a, b, c = attempt(1, "0.10"), attempt(2, "0.20"), attempt(1, "0.40")
    events = []
    for version, sequence, source, new, covered, steps, attempts in [
        (1, 40, 1, 2, (2, 3), (1,), (a, b)),
        (2, 100, 3, 4, (2, 4), (2,), (c,)),
    ]:
        start = 20 if version == 1 else 80
        events.append(
            stored(
                CompactionRequested(
                    provider="test",
                    model="test",
                    attempt=1,
                    source_snapshot_version=source,
                    covered_messages=covered,
                ),
                start,
            )
        )
        for offset, item in enumerate(attempts, 1):
            events.append(
                stored(CompactionAttemptRecorded(result=item), start + offset)
            )
        payload = CompactionCompleted(
            previous_version=None if version == 1 else 1,
            current_version=version,
            source_snapshot_version=source,
            source_snapshot_id=UUID(int=100 + source),
            source_event_sequence=start,
            new_snapshot_version=new,
            new_snapshot_id=UUID(int=100 + new),
            event_sequence=sequence,
            covered_messages=covered,
            covered_steps=steps,
            retained_first_step=version + 1,
            summary_sha256=sha256(rendered[version - 1].encode()).hexdigest(),
            provider="test",
            model="test",
            context_window_tokens=32768,
            output_reserve=1000,
            safety_margin=1024,
            business_budget=30000,
            summary_reserve=2048,
            old_input_tokens=25000,
            new_input_tokens=12000,
            summary_input_tokens=300,
            hard_trigger=False,
            attempts=attempts,
        )
        events.append(stored(payload, sequence))
    record = RunRecord(
        state=states[-1], last_event_sequence=100, latest_snapshot_version=4
    )
    envelope = RunEnvelope(run_id, 3, base.lifecycle.status, now, now, None, 100, 4)
    return (
        StoredRun(envelope, record, "unknown", ()),
        PageBoundary(
            run_id=run_id,
            as_of_sequence=100,
            latest_snapshot_version=4,
            run_status="created",
            read_at=now,
        ),
        CompactionRead(tuple(events), snapshots, {}, (), (), ()),
    )
