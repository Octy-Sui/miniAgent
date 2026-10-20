import sqlite3

from miniagent.tools.contracts import ToolStatus
from tests.support.runtime.tool_batch_resume import prefix


async def test_tool_query_projects_only_effective_result(tmp_path, monkeypatch):
    store, execution, _tool = await prefix(tmp_path, ToolStatus.SUCCESS)
    record = await store.get(execution.state.identity.run_id)
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    facts = await store.list_tool_results(
        record.state.identity.run_id,
        step_number=3,
        through_sequence=record.last_event_sequence,
    )
    assert len(facts) == 1
    assert facts[0].result.content == "confirmed outcome"
    assert facts[0].tool_call_id == "c0"
    queries = [sql for sql in statements if "FROM run_events" in sql]
    assert all("SELECT * FROM run_events" not in sql for sql in queries)
    assert any("json_extract" in sql and "$.result" in sql for sql in queries)
