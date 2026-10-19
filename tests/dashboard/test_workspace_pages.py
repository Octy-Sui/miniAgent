import html
import json
import re
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from miniagent.evaluation.contracts import EvaluationResult
from miniagent.runtime.events import EvaluationCompleted, NewRunEvent
from miniagent.storage import SQLiteRunStore
from tests.dashboard.builders import evaluation_example, report_document


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


async def test_workspace_survives_deleted_active_directory(
    database: Path, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    await publish_evaluation(database, root)
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        complete = await client.get(f"/runs/{UUID(int=1)}/workspace")
        assert complete.status_code == 200
        assert "已记录独立评测通过" in complete.text
        assert "目标测试清单未持久化，无法复核完整覆盖" in complete.text
        assert "diff --git" in complete.text
        assert "file:/" not in complete.text
        assert str(root) not in complete.text
        assert "visible recorded log" not in complete.text
        assert "当前工作目录是否仍存在：未检查" in complete.text


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


@pytest.mark.parametrize(("path", "value"), _INVALID_REPORTS)
async def test_report_type_variants_reach_workspace(
    database: Path, tmp_path: Path, path: tuple[str, ...], value
) -> None:
    document = report_document()
    parent = document["org__repo-1"]
    for key in path[:-1]:
        parent = parent[key]
    if value is _MISSING:
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    root = tmp_path / "artifacts"
    await publish_evaluation(database, root, report=document)
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(f"/runs/{UUID(int=1)}/workspace")
        assert response.status_code == 200
        assert "已记录独立评测通过" not in response.text
        assert "report_fields_invalid" in response.text
        assert "artifact_hash_mismatch" not in response.text


def boundary_token(text: str) -> str:
    match = re.search(r'data-boundary="([^"]+)"', text)
    assert match is not None
    return html.unescape(match[1])


async def test_workspace_partial_requests_preserve_database_boundary(
    database: Path, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    records = await publish_evaluation(database, root)
    store = FileArtifactStore(root)
    for n in range(100):
        await store.put(
            ArtifactWrite(
                run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
            ),
            f"log {n}".encode(),
        )
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        first = await client.get(f"/runs/{UUID(int=1)}/workspace")
        match = re.search(r'href="([^"]+)"[^>]*>更多 Artifact', first.text)
        assert match is not None
        page_link = html.unescape(match[1])
        await SQLiteRunStore(database).append(
            NewRunEvent(run_id=UUID(int=1), payload=EvaluationCompleted(passed=False)),
            expected_sequence=1,
        )
        partial = await client.get(page_link, headers={"HX-Request": "true"})
        assert partial.status_code == 200
        assert 'id="artifact-metadata"' in partial.text
        assert 'id="workspace-facts"' not in partial.text
        assert 'data-as-of="2"' not in partial.text
        assert (await client.get(page_link)).status_code == 200
        token = boundary_token(first.text)
        selected = await client.get(
            f"/runs/{UUID(int=1)}/workspace",
            params={"boundary": token, "patch": str(records[103].artifact_id)},
            headers={"HX-Request": "true"},
        )
        assert selected.status_code == 200
        assert "diff --git" in selected.text
        assert 'id="workspace-facts"' not in selected.text


async def test_preview_rechecks_content_and_cursor(
    database: Path, tmp_path: Path
) -> None:
    root = tmp_path / "artifacts"
    records = await publish_evaluation(database, root, long_log=True)
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        first = await client.get(f"/runs/{UUID(int=1)}/workspace")
        token = boundary_token(first.text)
        base = f"/runs/{UUID(int=1)}/artifacts/{records[107].artifact_id}"
        preview = await client.get(base, params={"boundary": token})
        assert preview.status_code == 200
        match = re.search(r'href="([^"]+)"[^>]*>继续预览', preview.text)
        assert match is not None
        next_link = html.unescape(match[1])
        assert (await client.get(next_link)).status_code == 200
        wrong = next_link.replace(
            str(records[107].artifact_id), str(records[108].artifact_id)
        )
        assert (await client.get(wrong)).status_code == 422
        path = root / "content" / records[107].sha256[:2] / records[107].sha256
        path.write_bytes(b"x" * records[107].size_bytes)
        assert (await client.get(next_link)).status_code == 409


async def test_optional_infra_failure_can_be_absent(
    database: Path, tmp_path: Path
) -> None:
    document = report_document()
    del document["org__repo-1"]["infra_failure"]
    root = tmp_path / "artifacts"
    await publish_evaluation(database, root, report=document)
    app = create_app(DashboardSettings(database=database, artifacts=root))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
        ) as client,
    ):
        response = await client.get(f"/runs/{UUID(int=1)}/workspace")
        assert response.status_code == 200
        assert "已记录独立评测通过" in response.text
        assert "目标测试清单未持久化，无法复核完整覆盖" in response.text
