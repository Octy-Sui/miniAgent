import asyncio
import hashlib
import os
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactWrite, FileArtifactStore


async def test_stream_writer_preserves_bytes_after_reopen(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    writer = await store.open_writer(ArtifactWrite(run_id=uuid4(), type="stdout"))
    chunk = bytes(range(256)) * 256
    for _ in range(128):
        await writer.write(chunk)
    ref = await writer.finish()
    expected = chunk * 128
    assert ref.size_bytes == len(expected)
    assert ref.sha256 == hashlib.sha256(expected).hexdigest()
    assert await FileArtifactStore(tmp_path).read(ref) == expected
    with pytest.raises(RuntimeError, match="closed"):
        await writer.write(b"late")


async def test_empty_stream_is_registered(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    writer = await store.open_writer(ArtifactWrite(run_id=uuid4(), type="stdout"))
    ref = await writer.finish()
    assert ref.size_bytes == 0
    assert await store.read(ref) == b""
    await writer.abort()
    assert await store.read(ref) == b""


async def test_abort_does_not_publish(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    run_id = uuid4()
    writer = await store.open_writer(ArtifactWrite(run_id=run_id, type="stdout"))
    await writer.write(b"prefix")
    await writer.abort()
    assert await store.list_for_run(run_id) == []
    assert not list(tmp_path.rglob("*.tmp"))
    with pytest.raises(RuntimeError, match="closed"):
        await writer.write(b"late")


@pytest.mark.parametrize("mode", ["writer", "put"])
async def test_tc_u12_01(tmp_path: Path, mode: str) -> None:
    store = FileArtifactStore(tmp_path)
    request = ArtifactWrite(run_id=uuid4(), type="stdout")
    deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(TimeoutError):
        if mode == "put":
            await store.put(request, b"prefix", deadline=deadline)
        else:
            await store.open_writer(request, deadline=deadline)
    assert await store.list_for_run(request.run_id) == []


@pytest.mark.parametrize("mode", ["writer", "put"])
@pytest.mark.parametrize("stage", ["body", "manifest", "published"])
@pytest.mark.parametrize("fail", [False, True])
async def test_tc_u12_03_and_04(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, stage: str, fail: bool
) -> None:
    store = FileArtifactStore(tmp_path)
    request = ArtifactWrite(run_id=uuid4(), type="stdout")
    entered = threading.Event()
    release = threading.Event()
    replace = os.replace

    def blocked_replace(source: str | Path, target: str | Path) -> None:
        manifest = Path(target).suffix == ".json"
        selected = (stage == "body" and not manifest) or (
            stage in {"manifest", "published"} and manifest
        )
        if selected:
            if stage == "published":
                replace(source, target)
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release not received")
            if fail and stage != "published":
                raise OSError("disk publication failure")
            if stage == "published":
                return
        replace(source, target)

    monkeypatch.setattr(os, "replace", blocked_replace)
    if mode == "writer":
        writer = await store.open_writer(request)
        await writer.write(b"evidence")
        task = asyncio.create_task(writer.finish())
    else:
        task = asyncio.create_task(store.put(request, b"evidence"))
    try:
        assert await asyncio.to_thread(entered.wait, 5), (
            "publication window not reached"
        )
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "I/O must settle before cancellation returns"
    finally:
        release.set()
    if fail and stage != "published":
        with pytest.raises(RuntimeError, match="persist") as error:
            await task
        assert isinstance(error.value.__cause__, OSError)
        assert await store.list_for_run(request.run_id) == []
    elif stage == "body":
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await store.list_for_run(request.run_id) == []
    else:
        ref = await task
        assert await FileArtifactStore(tmp_path).read(ref) == b"evidence"
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    await asyncio.sleep(0)
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before


async def test_write_failure_preserves_cause(tmp_path: Path, monkeypatch) -> None:
    store = FileArtifactStore(tmp_path)
    writer = await store.open_writer(ArtifactWrite(run_id=uuid4(), type="stdout"))

    def fail_write(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", fail_write)
    with pytest.raises(RuntimeError, match="persist") as error:
        await writer.write(b"prefix")
    assert isinstance(error.value.__cause__, OSError)
    await writer.abort()


@pytest.mark.parametrize("mode", ["writer", "put"])
async def test_tc_u12_01_cancel_during_body_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    store = FileArtifactStore(tmp_path)
    request = ArtifactWrite(run_id=uuid4(), type="stdout")
    entered = threading.Event()
    release = threading.Event()
    write = os.write

    def blocked_write(descriptor: int, data: bytes) -> int:
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test release not received")
        return write(descriptor, data)

    monkeypatch.setattr(os, "write", blocked_write)
    if mode == "writer":
        writer = await store.open_writer(request)
        task = asyncio.create_task(writer.write(b"prefix"))
    else:
        writer = None
        task = asyncio.create_task(store.put(request, b"prefix"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    if writer is not None:
        await writer.abort()
    assert await store.list_for_run(request.run_id) == []
    assert not list(tmp_path.rglob("*.tmp"))


async def test_deadline_after_body_prevents_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileArtifactStore(tmp_path)
    request = ArtifactWrite(run_id=uuid4(), type="stdout")
    loop = asyncio.get_running_loop()
    writer = await store.open_writer(request, deadline=loop.time() + 0.1)
    await writer.write(b"prefix")
    entered = threading.Event()
    release = threading.Event()
    fsync = os.fsync

    def blocked_fsync(descriptor: int) -> None:
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test release not received")
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", blocked_fsync)
    task = asyncio.create_task(writer.finish())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        await asyncio.sleep(0.11)
    finally:
        release.set()
    with pytest.raises(TimeoutError, match="deadline"):
        await task
    assert await store.list_for_run(request.run_id) == []
