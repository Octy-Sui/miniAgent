import argparse
import asyncio
from multiprocessing.connection import Connection
from pathlib import Path
from uuid import UUID

from miniagent.artifacts import FileArtifactStore
from miniagent.cli.candidate_submission import CandidateSubmission
from miniagent.environments import LocalWorkspaceBackend
from miniagent.environments.config import LocalWorkspaceSpec
from miniagent.runtime.events import RunEventType
from miniagent.runtime.execution import RunExecution, evaluation_scope
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.storage import SQLiteRunStore
from tests.acceptance.resume.conftest import receive
from tests.resume_support import StoreBoundary


async def run(args: argparse.Namespace, channel: Connection) -> None:
    real = SQLiteRunStore(args.database)

    class Gate(StoreBoundary):
        count = 0

        async def commit(self, change):
            identified = next(
                (
                    e.payload
                    for e in change.events
                    if e.type == RunEventType.OPERATION_EXECUTION_IDENTIFIED
                ),
                None,
            )
            self.count += int(identified is not None)
            selected = identified is not None and self.count == args.ordinal
            if selected and args.window == "before":
                channel.send({"identity": identified.identity.model_dump(mode="json")})
                await receive(channel)
            result = await self.store.commit(change)
            if selected and args.window == "after":
                channel.send({"identity": identified.identity.model_dump(mode="json")})
                await receive(channel)
            return result

    store = Gate(
        real,
        action="applied_then_error",
        event_type=RunEventType.PROVIDER_IDENTITY_CHANGED,
    )
    record = await store.get(args.run_id)
    context = record.recovery_context
    assert context and context.attachment and context.attachment.kind == "local"
    assert context.workspace_spec.kind == "local"
    artifacts = FileArtifactStore(args.root / "artifacts")
    backend = LocalWorkspaceBackend(
        root=context.attachment.root, artifact_store=artifacts
    )
    spec = LocalWorkspaceSpec(
        run_id=args.run_id, options=context.workspace_spec.options
    )
    workspace = await backend.attach(spec, context.attachment)
    execution = RunExecution.attach(
        record, RunJournal.attach(RunRecorder.attach(store, record), [])
    )
    with evaluation_scope(execution.evaluation_observer()):
        await CandidateSubmission(workspace, artifacts).submit(
            kind="candidate", candidate_text="candidate"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("run_id", type=UUID)
    parser.add_argument("root", type=Path)
    parser.add_argument("ordinal", type=int)
    parser.add_argument("window", choices=["before", "after"])
    parser.add_argument("channel", type=int)
    args = parser.parse_args()
    channel = Connection(args.channel)
    try:
        asyncio.run(run(args, channel))
    finally:
        channel.close()


if __name__ == "__main__":
    main()
