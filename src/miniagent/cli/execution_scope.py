from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from uuid import UUID

from miniagent.environments.managed_execution import cleanup_deadline_scope
from miniagent.runtime.interruption import InterruptionToken, interruption_scope
from miniagent.storage.run_lock import RunLock


@asynccontextmanager
async def application_execution_scope(
    database: Path,
    run_id: UUID,
    *,
    interruption_token: InterruptionToken | None = None,
) -> AsyncIterator[None]:
    with RunLock(database, run_id), ExitStack() as scope:
        if interruption_token is not None:
            scope.enter_context(interruption_scope(interruption_token))
            scope.enter_context(
                cleanup_deadline_scope(lambda: interruption_token.deadline)
            )
        yield
