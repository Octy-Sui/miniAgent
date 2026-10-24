import orjson
import pytest

from miniagent.environments.contracts import ExecutionResult, ExecutionStatus
from miniagent.tools.contracts import ToolOutputLimits, ToolResult, ToolStatus
from miniagent.tools.shell import ShellArguments, ShellTool
from tests.support.tools.contracts import EvidenceWorkspace


@pytest.mark.parametrize(
    ("execution_status", "exit_code", "expected_status"),
    [
        (ExecutionStatus.SUCCESS, 0, ToolStatus.SUCCESS),
        (ExecutionStatus.ERROR, 1, ToolStatus.ERROR),
        (ExecutionStatus.TIMEOUT, None, ToolStatus.TIMEOUT),
    ],
)
async def test_shell_result_stores_output_once_and_projects_model_message(
    execution_status: ExecutionStatus,
    exit_code: int | None,
    expected_status: ToolStatus,
) -> None:
    result = await ShellTool(
        EvidenceWorkspace(
            ExecutionResult(
                status=execution_status,
                exit_code=exit_code,
                stdout="hello\n",
                stderr="warning\n",
                duration_ms=12,
            )
        )
    ).execute(ShellArguments(command="example"), tool_call_id="call")

    metadata = result.tool_result_metadata()
    assert result.status is expected_status
    assert "content" not in metadata
    assert metadata["data"] == {
        "command": "example",
        "stdout": "hello\n",
        "stderr": "warning\n",
        "exit_code": exit_code,
    }
    message = result.to_response_item()
    assert message.tool_call_id == "call"
    assert orjson.loads(message.content)["data"] == metadata["data"]
    assert message.content.count("hello") == 1


def test_model_projection_does_not_truncate_persisted_result() -> None:
    result = ToolResult(
        tool_call_id="call",
        status=ToolStatus.SUCCESS,
        data={"stdout": "汉字\n" * 2000, "stderr": "failure details", "exit_code": 0},
    )
    message = result.to_response_item(
        ToolOutputLimits(max_content_bytes=1000, max_message_bytes=2048)
    )
    payload = orjson.loads(message.content)
    assert len(message.content.encode()) <= 2048
    assert payload["data"]["stderr"] == "failure details"
    assert payload["data"]["stdout"]
    metadata = result.tool_result_metadata()
    assert isinstance(metadata["data"], dict)
    assert metadata["data"]["stdout"] == "汉字\n" * 2000


def test_large_structured_data_is_bounded_without_losing_persisted_rows() -> None:
    result = ToolResult(
        tool_call_id="call",
        status=ToolStatus.SUCCESS,
        data={"rows": list(range(10000))},
    )

    message = result.to_response_item()
    payload = orjson.loads(message.content)
    assert len(message.content.encode()) <= ToolOutputLimits().max_message_bytes
    assert "data" in payload["output"]["omitted_fields"]
    assert result.data["rows"] == list(range(10000))


def test_message_budget_accounts_for_json_escaping_and_preserves_error_details() -> (
    None
):
    from miniagent.tools.contracts import ToolError

    text = '"\\\n汉' * 2000
    result = ToolResult(
        tool_call_id="call",
        status=ToolStatus.ERROR,
        data={"stdout": text, "stderr": "compile failed", "exit_code": 1},
        error=ToolError(code="COMMAND_FAILED", message="Command exited with code 1"),
    )
    message = result.to_response_item(
        ToolOutputLimits(max_content_bytes=2048, max_message_bytes=2048)
    )
    payload = orjson.loads(message.content)
    assert len(message.content.encode()) <= 2048
    assert text.startswith(payload["data"]["stdout"])
    assert payload["data"]["stdout"]
    assert payload["data"]["stderr"] == "compile failed"
    assert payload["data"]["exit_code"] == 1
    assert payload["error"]["message"] == "Command exited with code 1"
    assert payload["output"]["stdout_truncated"] is True
    assert result.data["stdout"] == text


def test_many_strings_can_be_omitted_without_losing_canonical_data() -> None:
    result = ToolResult(
        tool_call_id="call",
        status=ToolStatus.SUCCESS,
        data={"rows": ["row output" for _ in range(20000)]},
    )
    payload = orjson.loads(result.to_response_item().content)
    assert "data" in payload["output"]["omitted_fields"]
    assert result.data["rows"] == ["row output"] * 20000
