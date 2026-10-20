import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

CASES = [
    *(pytest.param(n, 16384, id=f"page-{n}") for n in (15, 16, 17, 32, 33)),
    *(pytest.param(128, size * 1024, id=f"body-{size}KiB") for size in (16, 64, 256)),
    *(pytest.param(n, 16384, id=f"count-{n}") for n in (128, 512, 2048)),
    pytest.param(128, 2 * 1024 * 1024, id="single-huge-object"),
    pytest.param(512, 65536, id="multiple-sources"),
]


@pytest.fixture(scope="module")
def prepared_history(tmp_path_factory):
    roots = {}
    parent = tmp_path_factory.mktemp("trajectory-resources")

    def prepare(count, body_bytes):
        key = (count, body_bytes)
        if key not in roots:
            root = parent / f"n{count}-b{body_bytes}"
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.support.trajectory.history",
                    str(root),
                    str(count),
                    str(body_bytes),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            assert process.returncode == 0, process.stdout + process.stderr
            metadata = json.loads(process.stdout)
            assert metadata["count"] == count
            assert metadata["context_window_tokens"] >= body_bytes
            roots[key] = root
        return roots[key]

    return prepare


@pytest.mark.parametrize("consumer", [f"g{number}" for number in range(1, 9)])
@pytest.mark.parametrize(("count", "body_bytes"), CASES)
async def test_tc_u14_all_consumers(prepared_history, count, body_bytes, consumer):
    root = prepared_history(count, body_bytes)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.trajectory.resource_worker",
        str(root),
        consumer,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    assert process.returncode == 0, stderr.decode()
    measured = json.loads(stdout)
    assert measured["oracle"] == "accepted"
    assert measured["retained_full_events"] == 0
    assert measured["max_live_events"] <= 17
    assert measured["rss_bytes"] > 0 and measured["wall_seconds"] > 0
    queries = measured["queries"]
    assert queries, "Every consumer requires actual SQL trace evidence"
    full_events = [sql for sql in queries if "SELECT * FROM run_events" in sql]
    for sql in full_events:
        if not re.search(r"event_id\s*=", sql):
            limit = re.search(r"LIMIT (\d+)", sql)
            assert limit and int(limit[1]) <= 16, sql
        assert re.search(rf"sequence\s*<=\s*{measured['boundary']}(?:\D|$)", sql), sql
    if consumer in ("g1", "g2"):
        assert all(
            "type IN ('compaction_completed')" in sql or "event_id =" in sql
            for sql in full_events
        )
    if consumer == "g8":
        assert not full_events
        assert any("AS effective_payload" in sql for sql in queries)
    evidence = Path(".superpowers/sdd/2026-10-20-trajectory")
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "resource-measurements.jsonl").open("a") as stream:
        stream.write(json.dumps(measured) + "\n")


@pytest.mark.parametrize(("count", "body_bytes"), CASES)
async def test_artifact_verification_runs_in_separate_process(
    prepared_history, count, body_bytes
):
    root = prepared_history(count, body_bytes)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.trajectory.resource_worker",
        str(root),
        "verify",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    assert process.returncode == 0, stderr.decode()
    verified = json.loads(stdout)
    assert verified["verified_artifacts"] > 0
    evidence = Path(".superpowers/sdd/2026-10-20-trajectory")
    with (evidence / "resource-verifications.jsonl").open("a") as stream:
        stream.write(json.dumps(verified) + "\n")
