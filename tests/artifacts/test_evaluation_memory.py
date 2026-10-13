import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("count", "size_mib"), [(1, 16), (4, 16), (8, 16), (4, 8), (4, 32)]
)
def test_large_log_registration_and_collection_memory(
    tmp_path: Path, count: int, size_mib: int
):
    script = """import asyncio, json, resource, sys
from pathlib import Path
from uuid import uuid4
from miniagent.artifacts import ArtifactWrite, FileArtifactStore
async def main():
    root, count, size = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    store = FileArtifactStore(root)
    run_id = uuid4()
    refs = []
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for number in range(count):
        content = bytes([number]) * size
        refs.append(await store.put(ArtifactWrite(run_id=run_id, type="evaluation_stdout"), content))
        del content
    registered = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    records = await store.list_for_run(run_id)
    assert {record.artifact_id for record in records} == {ref.artifact_id for ref in refs}
    assert all(record.size_bytes == size for record in records)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scale = 1 if sys.platform == "darwin" else 1024
    print(json.dumps({"count": count, "size": size, "baseline_rss": baseline * scale, "registration_rss": registered * scale, "collection_rss": peak * scale}))
asyncio.run(main())
"""
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "artifacts"),
            str(count),
            str(size_mib * 1024 * 1024),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    measurement = json.loads(process.stdout)
    assert measurement["count"] == count
    assert measurement["size"] == size_mib * 1024 * 1024
    print(json.dumps(measurement))
