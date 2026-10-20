import asyncio
import multiprocessing
import os
import socket
import time
from contextlib import chdir
from pathlib import Path

import httpx
import pytest

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings
from tests.support.dashboard.artifact_content import supervise
from tests.support.dashboard.builders import make_run
from tests.support.dashboard.manifests import manifest
from tests.support.dashboard.observation import probe_content
from tests.support.dashboard.workspace_pages import boundary_token


@pytest.mark.parametrize(
    ("location", "kind"),
    [
        (location, kind)
        for location in ("manifest", "content")
        for kind in ("fifo", "socket", "directory")
    ],
)
async def test_fifo_and_socket_are_rejected_without_waiting(
    database: Path, tmp_path: Path, location: str, kind: str
) -> None:
    await make_run(database, 1)
    root = tmp_path / "artifacts"
    record = manifest(root, 2)
    path = (
        root / "manifests" / record.run_id.hex / f"{record.artifact_id}.json"
        if location == "manifest"
        else root / "content" / record.sha256[:2] / record.sha256
    )
    path.unlink()
    node = None
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "socket":
        node = socket.socket(socket.AF_UNIX)
        with chdir(path.parent):
            node.bind(path.name)
    else:
        path.mkdir()
    try:
        started = time.monotonic()
        result = supervise(root, record.run_id, record.artifact_id)
        assert result["status"] == 409
        assert result["leaked"] == []
        assert time.monotonic() - started < 15
        app = create_app(DashboardSettings(database=database, artifacts=root))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8080"
            ) as client,
        ):
            page = await client.get(f"/runs/{record.run_id}/workspace")
            assert page.status_code == 200
            token = boundary_token(page.text)
            preview, light = await asyncio.gather(
                client.get(
                    f"/runs/{record.run_id}/artifacts/{record.artifact_id}",
                    params={"boundary": token},
                ),
                client.get("/health"),
            )
            assert preview.status_code == 409
            assert light.status_code == 200
    finally:
        if node is not None:
            node.close()


def replace_open_node(root: Path, relative: tuple[str, ...], *, symlink: bool):
    record = manifest(root, 2)
    original = b"visible-content"
    content_path = root / "content" / record.sha256[:2] / record.sha256
    content_identity = (content_path.stat().st_dev, content_path.stat().st_ino)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(
        target=probe_content,
        args=(str(root), str(record.run_id), str(record.artifact_id), child, relative),
    )
    process.start()
    child.close()
    try:
        assert parent.poll(15), "FD open not observed before supervisor deadline"
        opened = parent.recv()["opened"]
        assert opened[1] == relative
        target = root.joinpath(*relative)
        before = target.stat()
        assert (before.st_dev, before.st_ino) == tuple(opened[2:])
        moved = target.with_name(target.name + ".original")
        target.rename(moved)
        replacement = root.parent / "outside"
        if before.st_mode & 0o170000 == 0o040000:
            replacement.mkdir(exist_ok=True)
            if relative and relative[0] == "content":
                wrong = replacement / record.sha256
                wrong.write_bytes(b"replacement-sentinel")
            if symlink:
                target.symlink_to(replacement, target_is_directory=True)
            else:
                target.mkdir()
        else:
            replacement.write_bytes(b"replacement-sentinel")
            if symlink:
                target.symlink_to(replacement)
            else:
                target.write_bytes(b"replacement-sentinel")
        after = target.stat()
        assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        parent.send("continue")
        assert parent.poll(15), "product did not finish before supervisor deadline"
        result = parent.recv()
        process.join(1)
        assert process.exitcode == 0
        assert result.get("content") in (original, None)
        assert b"replacement-sentinel" not in (result.get("content") or b"")
        actual = [
            tuple(item[2:])
            for item in result["opened"]
            if item[1] == ("content", record.sha256[:2], record.sha256)
        ]
        assert all(identity == content_identity for identity in actual)
        assert result["leaked"] == []
        return result
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        parent.close()


@pytest.mark.parametrize("symlink", [False, True])
@pytest.mark.parametrize("location", ["manifest", "content"])
def test_open_content_survives_path_replacement(
    tmp_path: Path, symlink: bool, location: str
) -> None:
    root = tmp_path / "artifacts"
    record = manifest(root, 2)
    relative = (
        ("manifests", record.run_id.hex, f"{record.artifact_id}.json")
        if location == "manifest"
        else ("content", record.sha256[:2], record.sha256)
    )
    result = replace_open_node(root, relative, symlink=symlink)
    assert result["content"] == b"visible-content"


@pytest.mark.parametrize("symlink", [False, True])
@pytest.mark.parametrize("location", ["root", "manifests", "run", "content", "prefix"])
def test_open_directory_survives_node_replacement(
    tmp_path: Path, symlink: bool, location: str
) -> None:
    root = tmp_path / "artifacts"
    record = manifest(root, 2)
    relative = {
        "root": (),
        "manifests": ("manifests",),
        "run": ("manifests", record.run_id.hex),
        "content": ("content",),
        "prefix": ("content", record.sha256[:2]),
    }[location]
    result = replace_open_node(root, relative, symlink=symlink)
    assert result.get("content") in (b"visible-content", None)


def test_actual_oversize_reads_no_content(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    record = manifest(root, 2)
    target = root / "content" / record.sha256[:2] / record.sha256
    with target.open("wb") as stream:
        stream.truncate(20 * 1024 * 1024 + 1)
    result = supervise(root, record.run_id, record.artifact_id)
    assert result["content"] is None
    assert result["reads"] == result["hashes"] == result["leaked"] == []
    assert "read_limit_exceeded" in result["issues"]
