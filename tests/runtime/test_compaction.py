import sqlite3
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.runtime.context import validate_compaction_record, validate_summary
from miniagent.runtime.events import CompactionCompleted, NewRunEvent
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunPersistenceError, RunRecorder
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.runtime.state import ContextProgress
from miniagent.storage.sqlite import SQLiteRunStore
from test_context import summary_plan, summary_response


async def journal_candidate(tmp_path):
    plan = summary_plan()
    store = SQLiteRunStore(tmp_path / 'atomic.sqlite3')
    await store.initialize()
    recorder = RunRecorder(run_id=plan.state.identity.run_id, store=store, recovery_context=None)
    journal = RunJournal(recorder)
    await journal.create(plan.state)
    from miniagent.runtime.events import RunStarted
    await journal.start_run(plan.state, [NewRunEvent(run_id=plan.state.identity.run_id, payload=RunStarted())])
    plan = replace(plan, source_snapshot_version=1, source_event_sequence=1)
    conversation = await validate_summary(summary_response(), plan, store)
    source = await store.load_version(plan.state.identity.run_id, 1)
    assert source is not None
    state, event = journal.prepare_compaction(plan.state, conversation, plan, attempts=(), new_tokens=1000, source_snapshot_id=source.snapshot_id)
    return store, journal, plan, state, event


async def test_compaction_commit_is_immediate_and_reloadable(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    result = await journal.compact(state, event)
    assert result.snapshot.version == 2
    assert result.snapshot.snapshot_id == event.payload.new_snapshot_id
    assert result.snapshot.event_sequence == event.payload.event_sequence
    assert (await store.get(state.identity.run_id)).state.context.current_version == 1
    assert (await store.load_version(state.identity.run_id, 1)).state == plan.state
    assert await store.load_version(state.identity.run_id, 2) == result.snapshot
    await validate_compaction_record(await store.get(state.identity.run_id), store)


async def test_compaction_commit_conflict_is_atomic(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    confirmed = await store.save(NewRunSnapshot(run_id=state.identity.run_id, state=plan.state, event_sequence=1), expected_version=1)
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert await store.load_latest(state.identity.run_id) == confirmed
    assert len(await store.list_events(state.identity.run_id)) == 1


async def test_compaction_transaction_failure_is_atomic(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TRIGGER reject_snapshot BEFORE INSERT ON run_snapshots WHEN NEW.version = 2 BEGIN SELECT RAISE(ABORT, 'injected transaction failure'); END")
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert len(await store.list_events(state.identity.run_id)) == 1
    assert (await store.load_latest(state.identity.run_id)).version == 1


@pytest.mark.parametrize(('change',), [('current',), ('previous',), ('snapshot_id',), ('range',), ('hash',)])
async def test_corrupt_compaction_record_rejected(tmp_path, change) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    await journal.compact(state, event)
    import json
    with sqlite3.connect(store.path) as connection:
        if change in ('current', 'hash'):
            data = json.loads(connection.execute('SELECT state_json FROM runs').fetchone()[0])
            if change == 'current': data['context']['current_version'] = 99
            else: data['conversation']['messages'][2]['content'] = 'tampered summary'
            connection.execute('UPDATE runs SET state_json = ?', (json.dumps(data),))
        else:
            connection.execute("DROP TRIGGER run_events_no_update")
            data = json.loads(connection.execute("SELECT payload_json FROM run_events WHERE type='compaction_completed'").fetchone()[0])
            if change == 'previous': data['previous_version'] = 99
            if change == 'snapshot_id': data['new_snapshot_id'] = str(uuid4())
            if change == 'range': data['covered_messages'] = [2, 12]
            connection.execute("UPDATE run_events SET payload_json=? WHERE type='compaction_completed'", (json.dumps(data),))
    with pytest.raises(ValueError, match='compaction'):
        await validate_compaction_record(await store.get(state.identity.run_id), store)


async def test_normal_append_preserves_context_progress(tmp_path) -> None:
    from miniagent.models.protocol import FinishReason, ModelResponse
    from miniagent.runtime.transitions import accept_model_response

    store, journal, plan, state, event = await journal_candidate(tmp_path)
    await journal.compact(state, event)
    advanced = accept_model_response(state, ModelResponse(message=AssistantMessage(content='next'), finish_reason=FinishReason.STOP), None)
    assert advanced.context == ContextProgress(current_version=1)
    assert advanced.conversation.messages[:-1] == state.conversation.messages


async def test_compaction_rejects_inconsistent_step_metadata_atomically(tmp_path) -> None:
    store, journal, plan, state, event = await journal_candidate(tmp_path)
    event = event.model_copy(update={'payload': event.payload.model_copy(update={'covered_steps': (1, 2)})})
    with pytest.raises(RunPersistenceError):
        await journal.compact(state, event)
    assert (await store.get(state.identity.run_id)).state == plan.state
    assert len(await store.list_events(state.identity.run_id)) == 1
