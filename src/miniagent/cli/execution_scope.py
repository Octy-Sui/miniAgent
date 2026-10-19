from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from miniagent.storage.run_lock import RunLock


@asynccontextmanager
async def application_execution_scope(
    database: Path, run_id: UUID
) -> AsyncIterator[None]:
    with RunLock(database, run_id):
        yield
