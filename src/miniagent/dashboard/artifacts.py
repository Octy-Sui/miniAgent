import errno
import heapq
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from miniagent.artifacts.contracts import ArtifactRecord
from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import utc_now
from miniagent.dashboard.json_content import ContentError, parse_json
from miniagent.dashboard.reads import (
    ManifestItem,
    ManifestPage,
    PatchDiscovery,
    ReadBudget,
    ReadFailure,
    ReadIssue,
)

OpenFd = Callable[..., int]
_UNSAFE_ERRNOS = {errno.ELOOP, errno.ENOTDIR, errno.ENXIO, errno.ENODEV}


def _candidate(name: str) -> UUID | None:
    if not name.endswith(".json"):
        return None
    try:
        artifact_id = UUID(name[:-5])
    except ValueError:
        return None
    return artifact_id if name == f"{artifact_id}.json" else None


def _remember(issues: list[ReadIssue], issue: ReadIssue) -> None:
    if not any(existing.code == issue.code for existing in issues):
        issues.append(issue)


class ArtifactReader:
    def __init__(self, root: Path, *, open_fd: OpenFd = os.open) -> None:
        self.root = root.expanduser().resolve()
        self.open_fd = open_fd

    def _open(self, path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        try:
            return self.open_fd(path, flags, dir_fd=dir_fd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                raise ReadFailure(404, "artifact_path_missing") from exc
            if exc.errno in _UNSAFE_ERRNOS:
                raise ReadFailure(409, "artifact_unsafe") from exc
            raise

    @contextmanager
    def _directory(self, parts: tuple[str, ...]) -> Iterator[int]:
        descriptors = []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptors.append(self._open(self.root, flags))
            for part in parts:
                descriptors.append(self._open(part, flags, dir_fd=descriptors[-1]))
            yield descriptors[-1]
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @contextmanager
    def _regular(
        self, directory: int, name: str
    ) -> Iterator[tuple[int, os.stat_result]]:
        descriptor = self._open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ReadFailure(409, "artifact_not_regular")
            yield descriptor, info
        finally:
            os.close(descriptor)

    def _read_bytes(self, descriptor: int, size: int, budget: ReadBudget) -> bytes:
        content = bytearray()
        while len(content) < size:
            budget.check()
            chunk = os.read(descriptor, min(LIMITS.block_bytes, size - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        if len(content) != size or os.fstat(descriptor).st_size != size:
            raise ReadFailure(409, "artifact_changed")
        budget.check()
        return bytes(content)

    def _manifest(
        self, directory: int, run_id: UUID, artifact_id: UUID, budget: ReadBudget
    ) -> ManifestItem:
        budget.check()
        with self._regular(directory, f"{artifact_id}.json") as (descriptor, info):
            if info.st_size > LIMITS.manifest_bytes or not budget.reserve(info.st_size):
                return ManifestItem(
                    artifact_id,
                    None,
                    (
                        ReadIssue(
                            "read_limit_exceeded",
                            "manifest 超出读取上限",
                            subject_id=artifact_id,
                        ),
                    ),
                )
            content = self._read_bytes(descriptor, info.st_size, budget)
        try:
            data = parse_json(content, budget=budget)
            if (
                not isinstance(data, dict)
                or type(data.get("schema_version")) is not int
                or data["schema_version"] != 1
            ):
                return ManifestItem(
                    artifact_id,
                    None,
                    (
                        ReadIssue(
                            "manifest_unsupported",
                            "manifest 版本不支持",
                            subject_id=artifact_id,
                        ),
                    ),
                )
            record = ArtifactRecord.model_validate_json(content, strict=True)
            if record.run_id != run_id or record.artifact_id != artifact_id:
                return ManifestItem(
                    artifact_id,
                    None,
                    (
                        ReadIssue(
                            "manifest_inconsistent",
                            "manifest 归属或身份不一致",
                            subject_id=artifact_id,
                        ),
                    ),
                )
            return ManifestItem(artifact_id, record, ())
        except (ContentError, ValidationError):
            return ManifestItem(
                artifact_id,
                None,
                (
                    ReadIssue(
                        "manifest_unsupported",
                        "manifest 内容不支持",
                        subject_id=artifact_id,
                    ),
                ),
            )

    def list_manifests(
        self, run_id: UUID, *, after_id: UUID | None, budget: ReadBudget
    ) -> ManifestPage:
        heap: list[tuple[int, UUID]] = []
        issues: list[ReadIssue] = []
        complete = True
        items = []
        try:
            with self._directory(("manifests", run_id.hex)) as directory:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if budget.monotonic() >= budget.deadline:
                            complete = False
                            _remember(
                                issues, ReadIssue("not_loaded", "目录发现尚未完成")
                            )
                            break
                        artifact_id = _candidate(entry.name)
                        if artifact_id is None:
                            _remember(
                                issues,
                                ReadIssue(
                                    "unexpected_manifest_entry", "发现非登记目录项"
                                ),
                            )
                            continue
                        if after_id is not None and artifact_id <= after_id:
                            continue
                        if len(heap) < LIMITS.metadata_page + 1:
                            heapq.heappush(heap, (-artifact_id.int, artifact_id))
                        elif artifact_id.int < -heap[0][0]:
                            heapq.heapreplace(heap, (-artifact_id.int, artifact_id))
                selected = sorted(artifact_id for _, artifact_id in heap)
                for artifact_id in selected[: LIMITS.metadata_page]:
                    try:
                        items.append(
                            self._manifest(directory, run_id, artifact_id, budget)
                        )
                    except ReadFailure as exc:
                        if exc.code == "read_deadline":
                            complete = False
                            _remember(
                                issues, ReadIssue("not_loaded", "目录读取尚未完成")
                            )
                        items.append(
                            ManifestItem(
                                artifact_id,
                                None,
                                (
                                    ReadIssue(
                                        exc.code,
                                        "登记项未完成读取",
                                        subject_id=artifact_id,
                                    ),
                                ),
                            )
                        )
        except ReadFailure as exc:
            if exc.status_code == 404:
                return ManifestPage((), None, utc_now(), True)
            return ManifestPage(
                (),
                None,
                utc_now(),
                False,
                (ReadIssue(exc.code, "登记目录不可安全读取"),),
            )
        next_id = (
            items[-1].artifact_id
            if complete and len(heap) > LIMITS.metadata_page
            else None
        )
        return ManifestPage(tuple(items), next_id, utc_now(), complete, tuple(issues))

    def read_manifest(
        self, run_id: UUID, artifact_id: UUID, *, budget: ReadBudget
    ) -> ManifestItem:
        budget.check()
        with self._directory(("manifests", run_id.hex)) as directory:
            return self._manifest(directory, run_id, artifact_id, budget)

    def discover_patches(self, run_id: UUID, *, budget: ReadBudget) -> PatchDiscovery:
        items = []
        issues: list[ReadIssue] = []
        complete = True
        try:
            with (
                self._directory(("manifests", run_id.hex)) as directory,
                os.scandir(directory) as entries,
            ):
                for entry in entries:
                    if budget.monotonic() >= budget.deadline:
                        complete = False
                        _remember(issues, ReadIssue("not_loaded", "补丁发现尚未完成"))
                        break
                    artifact_id = _candidate(entry.name)
                    if artifact_id is None:
                        complete = False
                        _remember(
                            issues,
                            ReadIssue(
                                "unexpected_manifest_entry",
                                "存在非登记目录项，无法证明补丁唯一",
                            ),
                        )
                        continue
                    try:
                        item = self._manifest(directory, run_id, artifact_id, budget)
                    except ReadFailure as exc:
                        complete = False
                        _remember(issues, ReadIssue(exc.code, "部分登记项不可读取"))
                        if exc.code == "read_deadline":
                            break
                        continue
                    if item.record is None:
                        complete = False
                        for issue in item.issues:
                            _remember(issues, issue)
                    elif item.record.type == "workspace_patch":
                        items.append(item)
        except ReadFailure as exc:
            if exc.status_code == 404:
                return PatchDiscovery((), True, ())
            return PatchDiscovery(
                (), False, (ReadIssue(exc.code, "补丁登记目录不可安全读取"),)
            )
        return PatchDiscovery(
            tuple(sorted(items, key=lambda item: item.artifact_id)),
            complete,
            tuple(issues),
        )
