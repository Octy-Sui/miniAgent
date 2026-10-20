from datetime import UTC, datetime
from pathlib import Path

from miniagent.dashboard.cursors import PageBoundary
from miniagent.runtime.events import NewRunEvent, ToolEnded, ToolStarted
from miniagent.storage import SQLiteRunStore
from miniagent.tools.contracts import ToolOutputInfo, ToolResult, ToolReturnValidation
from tests.support.dashboard.builders import make_run


async def tool_page(database: Path):
    run_id = await make_run(database, 1)
    store = SQLiteRunStore(database)
    sequence = 0
    for step in (1, 2):
        for payload in (
            ToolStarted(
                call_index=0, tool_call_id="same", tool_name="shell", arguments={}
            ),
            ToolEnded(
                call_index=0,
                tool_call_id="same",
                tool_name="shell",
                status="success",
                result=ToolResult(
                    tool_call_id="same",
                    status="success",
                    content="token=private-value\nvisible",
                    data={"exit_code": 0},
                    output=ToolOutputInfo(stdout_truncated=True),
                ),
                raw_result=None,
                return_validation=ToolReturnValidation(
                    disposition="not_returned", reason="fixture_not_returned"
                ),
                observation=None,
                observation_missing_reason="fixture_not_observed",
            ),
        ):
            await store.append(
                NewRunEvent(
                    run_id=run_id,
                    payload=payload,
                    step_number=step,
                    span_id=f"{step:016x}",
                ),
                expected_sequence=sequence,
            )
            sequence += 1
    return PageBoundary(
        run_id=run_id,
        as_of_sequence=4,
        latest_snapshot_version=0,
        run_status="created",
        read_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
