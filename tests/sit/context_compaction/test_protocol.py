import os
import sys

from tests.support.context_compaction.cases import manifest
from tests.support.context_compaction.protocol import (
    AcceptanceManifest,
    freeze_manifest,
)


async def test_live_runner_executes_approved_local_write_with_real_composition(
    tmp_path, monkeypatch, prepared_source
) -> None:
    import json

    import httpx2
    from openai import AsyncOpenAI

    from tests.support.context_compaction.live import execute

    source, initial, revision = prepared_source
    data = manifest("independent").model_dump()
    data.update(
        model={"provider": "qwen", "model": "qwen3.8-flash"},
        runner_revision=revision,
        max_output_tokens=976,
    )
    data["task_oracles"][0].update(
        source_dir=str(source),
        initial_workspace_revision=initial,
        expected_files={"source.txt": "source\n", "result.txt": "blue"},
        allowed_files=["source.txt", "result.txt"],
    )
    directory = freeze_manifest(
        AcceptanceManifest.model_validate(data), tmp_path / "evidence"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RUN_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    counter = 0

    def endpoint(request):
        nonlocal counter
        counter += 1
        message = (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": json.dumps(
                                {"command": "printf blue > result.txt"}
                            ),
                        },
                    }
                ],
            }
            if counter == 1
            else {"role": "assistant", "content": "done"}
        )
        return httpx2.Response(
            200,
            json={
                "id": f"request-{counter}",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen3.8-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if counter == 1 else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    def sdk_client(**options):
        return AsyncOpenAI(
            **options,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(endpoint)),
        )

    monkeypatch.setattr("miniagent.providers.qwen.AsyncOpenAI", sdk_client)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"y\n")
    os.close(write_fd)
    with os.fdopen(read_fd) as console_input:
        monkeypatch.setattr(sys, "stdin", console_input)
        await execute(directory, "compacted-1")
    folder = directory / "runs" / "compacted-1"
    result = json.loads((folder / "result.json").read_text())
    assert result["completed"]
    assert result["task_result"]["passed"]
    assert counter == 2
    assert len((folder / "wire.jsonl").read_text().splitlines()) == 4
    assert "approved" in (folder / "approval-trace.jsonl").read_text()
    from tests.support.context_compaction.protocol import (
        RunEvidence,
        _validate_raw_evidence,
    )

    _validate_raw_evidence(folder, RunEvidence.model_validate(result))
