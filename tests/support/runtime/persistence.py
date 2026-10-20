from dataclasses import dataclass

import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.storage import SQLiteRunStore


@dataclass(frozen=True)
class RuntimePersistence:
    store: SQLiteRunStore
    artifacts: FileArtifactStore


@pytest.fixture
async def runtime_persistence(tmp_path):
    store = SQLiteRunStore(tmp_path / "runtime.sqlite3")
    await store.initialize()
    return RuntimePersistence(store, FileArtifactStore(tmp_path / "runtime-artifacts"))
