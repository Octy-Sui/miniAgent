import asyncio
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from playwright.sync_api import sync_playwright

from miniagent.artifacts import FileArtifactStore
from miniagent.artifacts.contracts import ArtifactWrite
from miniagent.storage import SQLiteRunStore
from tests.support.dashboard.builders import evaluation_example, make_database, make_run
from tests.support.dashboard.detail_pages import populated
from tests.support.dashboard.workspace_pages import publish_evaluation


@dataclass(frozen=True)
class BrowserSite:
    url: str
    database: Path
    artifacts: Path
    records: dict


@pytest.fixture
def site(tmp_path: Path):
    database = asyncio.run(make_database(tmp_path / "runs.sqlite3"))
    artifacts = tmp_path / "artifacts"
    asyncio.run(populated(database))
    records = asyncio.run(
        publish_evaluation(database, artifacts, long_log=True, persist_run=False)
    )
    asyncio.run(make_run(database, 2, task="second task"))
    terminal = evaluation_example(artifacts).run.record
    assert terminal is not None
    terminal_state = terminal.state.model_copy(
        update={
            "identity": terminal.state.identity.model_copy(
                update={"run_id": UUID(int=3)}
            )
        }
    )

    async def create_terminal() -> None:
        await SQLiteRunStore(database).create(terminal_state)

    asyncio.run(create_terminal())
    sentinel = '<script>window.dashboardSentinel=1</script><img src="https://evil.invalid/pixel" onerror="window.dashboardSentinel=2">'
    records["sentinel"] = asyncio.run(
        FileArtifactStore(artifacts).put(
            ArtifactWrite(
                run_id=UUID(int=1), type="evaluation_stdout", media_type="text/plain"
            ),
            sentinel.encode(),
        )
    )
    with serve_site(database, artifacts, records) as running:
        yield running


@contextmanager
def serve_site(database: Path, artifacts: Path, records: dict):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.support.dashboard.server",
            "--database",
            str(database),
            "--artifacts",
            str(artifacts),
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(url + "/health", timeout=0.5) as response:
                    assert response.status == 200
                break
            except (urllib.error.URLError, TimeoutError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("dashboard acceptance server did not start")
                time.sleep(0.05)
        yield BrowserSite(url, database, artifacts, records)
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def page(site: BrowserSite, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(5000)
    try:
        yield page
    finally:
        context.close()


@pytest.fixture
async def database(tmp_path: Path) -> Path:
    return await make_database(tmp_path / "runs.sqlite3")
