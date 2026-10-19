from dataclasses import replace
from decimal import Decimal
from hashlib import sha256

from miniagent.dashboard.projections.compaction import group_compactions, project_memory
from miniagent.runtime.events import CompactionCompleted
from tests.dashboard.builders import memory_example


def test_summary_hashes_full_rendered_message() -> None:
    run, boundary, facts = memory_example()
    memory = project_memory(run, boundary, facts)
    assert memory.version == 2
    assert memory.issues == ()
    assert {field.key: field.value for field in memory.fields}[
        "current_goal"
    ] == "goal 2"
    assert run.record is not None
    message = run.record.state.conversation.messages[2]
    assert message.content is not None
    suffix = message.content.split("\n", 1)[1]
    changed = []
    for item in facts.events:
        if (
            item.event is not None
            and isinstance(item.event.payload, CompactionCompleted)
            and item.event.payload.current_version == 2
        ):
            item = replace(
                item,
                event=item.event.model_copy(
                    update={
                        "payload": item.event.payload.model_copy(
                            update={
                                "summary_sha256": sha256(suffix.encode()).hexdigest()
                            }
                        )
                    }
                ),
            )
        changed.append(item)
    bad = project_memory(run, boundary, replace(facts, events=tuple(changed)))
    assert "summary_hash_mismatch" in {issue.code for issue in bad.issues}


def test_compaction_attempts_do_not_cross_groups() -> None:
    _, _, facts = memory_example()
    groups = group_compactions(facts.events)
    assert [group.first_requested_sequence for group in groups] == [20, 80]
    assert groups[0].total_cost_usd == Decimal("0.30")
    assert groups[1].total_cost_usd == Decimal("0.40")
    assert len(groups[0].attempts) == 2


from miniagent.dashboard.projections.compaction import project_historical_memory


def test_current_and_historical_summary_are_distinct() -> None:
    run, boundary, facts = memory_example()
    current = project_memory(run, boundary, facts)
    historical = project_historical_memory(facts.snapshots[2], boundary, facts)
    assert current.version == 2
    assert historical.version == 1
    assert {field.key: field.value for field in historical.fields}[
        "current_goal"
    ] == "goal 1"


from miniagent.runtime.events import CompactionAttemptRecorded


def test_unloaded_attempt_cannot_prove_group_total() -> None:
    _, _, facts = memory_example()
    events = tuple(
        replace(item, event=None) if item.sequence == 21 else item
        for item in facts.events
    )
    group = group_compactions(events)[0]
    assert "not_loaded" in {issue.code for issue in group.issues}
    assert group.total_cost_usd is None


def test_conflicting_attempt_prices_remain_unknown() -> None:
    _, _, facts = memory_example()
    events = []
    for item in facts.events:
        if item.sequence == 21:
            assert item.event is not None and isinstance(
                item.event.payload, CompactionAttemptRecorded
            )
            attempt = item.event.payload.result
            assert attempt.request_cost is not None
            price = attempt.request_cost.model_copy(
                update={"input_cost": Decimal("0.99"), "total_cost": Decimal("0.99")}
            )
            payload = item.event.payload.model_copy(
                update={"result": attempt.model_copy(update={"request_cost": price})}
            )
            item = replace(
                item, event=item.event.model_copy(update={"payload": payload})
            )
        events.append(item)
    group = group_compactions(tuple(events))[0]
    assert group.total_cost_usd is None
    assert "attempt_conflict" in {issue.code for issue in group.attempts[0].issues}


from miniagent.runtime.state import ContextProgress


def test_explicit_no_summary_does_not_override_completed_fact() -> None:
    run, boundary, facts = memory_example()
    assert run.record is not None
    state = run.record.state.model_copy(update={"context": ContextProgress()})
    run = replace(run, record=run.record.model_copy(update={"state": state}))
    memory = project_memory(run, boundary, facts)
    assert "compaction_inconsistent" in {issue.code for issue in memory.issues}
    assert "尚未生成持久化 Task Memory" not in [field.value for field in memory.fields]
