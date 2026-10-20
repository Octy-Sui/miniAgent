from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings


@asynccontextmanager
async def client_for(database: Path, root: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        yield client
