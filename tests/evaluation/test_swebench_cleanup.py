import asyncio
import json
import os

import pytest


async def test_cancel_reaps_process_group_and_only_attempt_containers(evaluation_case):
    evaluator, request, store = evaluation_case
    root = evaluator.executable.parent
    (root / "harness-scenario.json").write_text(
        json.dumps({"hang": True, "ignore_term": True})
    )
    evaluating = asyncio.create_task(evaluator.evaluate(request))
    while not (root / "harness-started").exists() and not evaluating.done():
        await asyncio.sleep(0.01)
    evaluating.cancel()
    with pytest.raises(asyncio.CancelledError):
        await evaluating
    for name in ("harness-started", "harness-child"):
        with pytest.raises(ProcessLookupError):
            os.kill(int((root / name).read_text()), 0)
    containers = json.loads((root / "docker-scenario.json").read_bytes())["containers"]
    assert [container["Id"] for container in containers] == ["neighbor"]
    assert "evaluation_summary" in {
        record.type for record in await store.list_for_run(request.run_id)
    }


async def test_daemon_change_prevents_deletion(evaluation_case):
    evaluator, request, store = evaluation_case
    root = evaluator.executable.parent
    (root / "harness-scenario.json").write_text(json.dumps({"daemon_change": True}))
    result = await evaluator.evaluate(request)
    assert result.passed is False
    summary = json.loads(
        await store.read(
            next(ref for ref in result.artifacts if ref.type == "evaluation_summary")
        )
    )
    assert "DOCKER_DAEMON_CHANGED" in " ".join(summary["diagnostics"])
    calls = [
        json.loads(line)["argv"]
        for line in (root / "docker-calls.jsonl").read_text().splitlines()
    ]
    assert not any("rm" in call for call in calls)


async def test_process_timeout_cleans_resources(evaluation_case):
    evaluator, request, _ = evaluation_case
    evaluator.settings.swebench_process_timeout_seconds = 2
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"hang": True})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == "EVALUATION_PROCESS_TIMEOUT"
