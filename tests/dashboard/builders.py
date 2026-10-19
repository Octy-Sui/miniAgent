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
