import html
import json
import re
from pathlib import Path
from uuid import UUID

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import EvaluationCompleted, NewRunEvent
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import evaluation_example


async def publish_evaluation(
    database: Path,
    root: Path,
    *,
    report: dict | None = None,
    long_log: bool = False,
    persist_run: bool = True,
    attempt_id: UUID | None = None,
    submission_id: UUID | None = None,
    config_patch_sha256: str | None = None,
):
    example = evaluation_example(root)
    attempt_id = attempt_id or UUID(int=101)
    submission_id = submission_id or UUID(int=102)
    store = FileArtifactStore(root)
    records = {}
    refs = []
    for number in range(103, 111):
        content = example.contents[UUID(int=number)]
        body = content.content
        assert body is not None
        metadata = dict(content.record.metadata)
        if "attempt_id" in metadata:
            metadata["attempt_id"] = str(attempt_id)
        if "submission_id" in metadata:
            metadata["submission_id"] = str(submission_id)
        if number in (104, 105):
            document = json.loads(body)
            document["attempt_id"] = str(attempt_id)
            document["submission_id"] = str(submission_id)
            document["patch_artifact_id"] = str(records[103].artifact_id)
            if number == 104:
                document["patch_ref"] = records[103].reference().model_dump(mode="json")
                if config_patch_sha256 is not None:
                    document["patch_sha256"] = config_patch_sha256
            body = json.dumps(document).encode()
        if number == 106 and report is not None:
            body = json.dumps(report).encode()
        if number == 107 and long_log:
            body = b"visible log\n" * 5000
        ref = await store.put(
            ArtifactWrite(
                run_id=example.boundary.run_id,
                type=content.record.type,
                name=content.record.name,
                media_type=content.record.media_type,
                metadata=metadata,
            ),
            body,
        )
        records[number] = await store.get_record(ref)
        refs.append(ref)
    if not persist_run:
        return records
    assert example.run.record is not None
    run_store = SQLiteRunStore(database)
    await run_store.create(
        example.run.record.state, recovery_context=example.run.record.recovery_context
    )
    await run_store.append(
        NewRunEvent(
            run_id=example.boundary.run_id,
            payload=EvaluationCompleted(
                passed=True,
                result=EvaluationResult(passed=True, output="done", artifacts=refs),
            ),
            artifacts=refs,
        ),
        expected_sequence=0,
    )
    return records


_BOOL_FIELDS = (
    "patch_exists",
    "patch_is_None",
    "patch_successfully_applied",
    "resolved",
    "infra_failure",
)

_MISSING = object()

_INVALID_REPORTS = [
    *(((field,), value) for field in _BOOL_FIELDS for value in ("true", 1, None)),
    *(
        (("tests_status", group, field), value)
        for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
        for field in ("success", "failure")
        for value in ("not a list", [1])
    ),
    (("tests_status",), None),
    (("tests_status",), []),
    (("tests_status", "FAIL_TO_PASS"), None),
    (("tests_status", "PASS_TO_PASS"), []),
]

_INVALID_REPORTS.extend(
    [
        *(((field,), _MISSING) for field in _BOOL_FIELDS[:-1]),
        *(
            (("tests_status", group), _MISSING)
            for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
        ),
        *(
            (("tests_status", group, field), _MISSING)
            for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
            for field in ("success", "failure")
        ),
        *((("tests_status", group), []) for group in ("FAIL_TO_PASS", "PASS_TO_PASS")),
        *(
            (("tests_status", group, "failure"), {})
            for group in ("FAIL_TO_PASS", "PASS_TO_PASS")
        ),
    ]
)


def boundary_token(text: str) -> str:
    match = re.search(r'data-boundary="([^"]+)"', text)
    assert match is not None
    return html.unescape(match[1])
