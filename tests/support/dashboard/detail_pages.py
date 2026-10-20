import sqlite3
from contextlib import closing
from pathlib import Path

from miniagent.models.messages import UserMessage
from miniagent.runtime.events import NewRunEvent, RunStarted
from miniagent.runtime.snapshots import NewRunSnapshot
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import make_run


async def populated(database: Path):
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    events = []
    for n in range(240):
        events.append(
            await store.append(
                NewRunEvent(run_id=run_id, payload=RunStarted()), expected_sequence=n
            )
        )
    record = await store.get(run_id)
    state = record.state.model_copy(
        update={
            "conversation": record.state.conversation.model_copy(
                update={
                    "messages": (
                        UserMessage(content="requested-message"),
                        UserMessage(content="unrequested-message"),
                    )
                }
            )
        }
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE runs SET state_json=?", (state.model_dump_json(),))
        connection.commit()
    for version in range(2):
        await store.save(
            NewRunSnapshot(run_id=run_id, event_sequence=240, state=state),
            expected_version=version,
        )
    return run_id, store, events
