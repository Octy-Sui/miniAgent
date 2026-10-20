from pathlib import Path

import pytest

from tests.support.dashboard.builders import make_database


@pytest.fixture
async def database(tmp_path: Path) -> Path:
    return await make_database(tmp_path / "runs.sqlite3")
