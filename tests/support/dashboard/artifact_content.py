import multiprocessing
from pathlib import Path
from uuid import UUID

from tests.support.dashboard.observation import probe_content


def supervise(root: Path, run_id: UUID, artifact_id: UUID):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=probe_content, args=(str(root), str(run_id), str(artifact_id), writer)
    )
    process.start()
    writer.close()
    try:
        assert reader.poll(15), "product did not finish before supervisor deadline"
        result = reader.recv()
        process.join(1)
        assert process.exitcode == 0
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        reader.close()
