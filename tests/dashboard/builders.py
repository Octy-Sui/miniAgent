from pathlib import Path

from miniagent.storage import SQLiteRunStore


async def make_database(path: Path) -> Path:
    store = SQLiteRunStore(path)
    await store.initialize()
    return path
