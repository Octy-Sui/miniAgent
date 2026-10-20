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


async def test_large_model_request_does_not_enter_effective_result_decode(
    tmp_path, monkeypatch
):
    import json

    store, execution, _tool = await prefix(tmp_path, ToolStatus.SUCCESS)
    record = await store.get(execution.state.identity.run_id)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TRIGGER run_events_no_update")
        row = connection.execute(
            "SELECT sequence,payload_json FROM run_events WHERE type='model_requested' ORDER BY sequence LIMIT 1"
        ).fetchone()
        payload = json.loads(row[1])
        payload["request"]["messages"][0]["content"] = "model payload" * 200000
        connection.execute(
            "UPDATE run_events SET payload_json=? WHERE sequence=?",
            (json.dumps(payload), row[0]),
        )
    statements = []
    connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    facts = await store.list_tool_results(record.state.identity.run_id, step_number=3)
    assert facts[0].result.content == "confirmed outcome"
    assert not any("SELECT * FROM run_events" in sql for sql in statements)
