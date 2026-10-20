from pathlib import Path

from miniagent.dashboard.parameters import EventFilters
from miniagent.dashboard.queries.runs import read_run_page
from miniagent.dashboard.reads import ReadBudget
from tests.support.dashboard.builders import make_run


async def recorded_run(database: Path):
    run_id = await make_run(database, 1)
    return read_run_page(
        database, run_id, EventFilters(), budget=ReadBudget(5, monotonic=lambda: 0)
    )
