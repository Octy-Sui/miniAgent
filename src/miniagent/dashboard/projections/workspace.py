import re
from dataclasses import dataclass, field
from uuid import UUID

from miniagent.dashboard.reads import PatchDiscovery, ReadBudget, ReadFailure, ReadIssue
from miniagent.dashboard.redaction import redact_text
from miniagent.dashboard.views import DiffFileView, DiffView, PatchChoice

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_ESCAPES = {
    "a": 7,
    "b": 8,
    "t": 9,
    "n": 10,
    "v": 11,
    "f": 12,
    "r": 13,
    '"': 34,
    "\\": 92,
}


def choose_patch(
    discovery: PatchDiscovery,
    associated_patch_ids: tuple[UUID, ...],
    *,
    association_conflict: bool = False,
) -> PatchChoice:
    if not discovery.complete or association_conflict:
        return PatchChoice(
            None, "扫描不完整或评测关联冲突，请显式选择", discovery.issues
        )
    candidates = {
        item.artifact_id
        for item in discovery.items
        if item.record is not None and item.record.type == "patch" and not item.issues
    }
    linked = set(associated_patch_ids)
    if linked:
        if len(linked) == 1 and linked <= candidates:
            return PatchChoice(next(iter(linked)), "评测明确关联的 patch", ())
        return PatchChoice(None, "评测关联不唯一或未登记，请显式选择", ())
    if len(candidates) == 1:
        return PatchChoice(next(iter(candidates)), "唯一登记的 patch", ())
    return PatchChoice(None, "没有唯一 patch，请显式选择", ())


def _path(value: str, *, prefix: bool = False) -> str | None:
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        if not _QUOTED.fullmatch(value):
            raise ValueError("invalid quoted path")
        raw = bytearray()
        index = 1
        while index < len(value) - 1:
            char = value[index]
            index += 1
            if char != "\\":
                raw.extend(char.encode("utf-8"))
                continue
            char = value[index]
            index += 1
            if char in "01234567":
                digits = char
                while (
                    index < len(value) - 1
                    and len(digits) < 3
                    and value[index] in "01234567"
                ):
                    digits += value[index]
                    index += 1
                raw.append(int(digits, 8))
            elif char in _ESCAPES:
                raw.append(_ESCAPES[char])
            else:
                raise ValueError("invalid git escape")
        value = raw.decode("utf-8")
    if prefix:
        if value[:2] not in {"a/", "b/"}:
            raise ValueError("invalid diff prefix")
        value = value[2:]
    if not value or "\x00" in value:
        raise ValueError("invalid diff path")
    return value


def _header(value: str) -> tuple[str | None, str | None]:
    if value.startswith('"'):
        paths = list(_QUOTED.finditer(value))
        if len(paths) == 2 and value == paths[0][0] + " " + paths[1][0]:
            return _path(paths[0][0], prefix=True), _path(paths[1][0], prefix=True)
    if value.startswith("a/") and value.count(" b/") == 1:
        left, right = value.split(" b/", 1)
        return _path(left, prefix=True), _path("b/" + right, prefix=True)
    raise ValueError("ambiguous git header")


@dataclass
class _File:
    old: str | None = None
    new: str | None = None
    kind: str = "modify"
    lines: list[str] = field(default_factory=list)
    evidence: bool = False

    def view(self) -> DiffFileView:
        return DiffFileView(
            redact_text(self.old) if self.old is not None else None,
            redact_text(self.new) if self.new is not None else None,
            self.kind,
            redact_text("".join(self.lines)),
        )


def parse_diff(text: str, *, budget: ReadBudget) -> DiffView:
    files: list[_File] = []
    current: _File | None = None
    complete = True
    remaining: tuple[int, int] | None = None
    issues: list[ReadIssue] = []
    try:
        for line in text.splitlines(keepends=True):
            budget.check()
            value = line.rstrip("\r\n")
            if value.startswith("diff --git "):
                if remaining is not None and remaining != (0, 0):
                    complete = False
                current = _File()
                files.append(current)
                current.old, current.new = _header(value[11:])
                remaining = None
                current.lines.append(line)
                continue
            if current is None:
                current = _File(kind="unknown")
                files.append(current)
                complete = False
            current.lines.append(line)
            match = _HUNK.fullmatch(value)
            if match:
                if remaining is not None and remaining != (0, 0):
                    complete = False
                remaining = (int(match[2] or "1"), int(match[4] or "1"))
                current.evidence = True
            elif remaining is not None and value[:1] in {" ", "+", "-"}:
                old, new = remaining
                remaining = (old - (value[0] != "+"), new - (value[0] != "-"))
                if min(remaining) < 0:
                    complete = False
            elif value == "\\ No newline at end of file":
                if remaining is None:
                    complete = False
            elif remaining is not None and remaining != (0, 0):
                complete = False
            elif value.startswith("--- "):
                current.old = _path(value[4:], prefix=value[4:] != "/dev/null")
            elif value.startswith("+++ "):
                current.new = _path(value[4:], prefix=value[4:] != "/dev/null")
                if current.old is None:
                    current.kind = "add"
                if current.new is None:
                    current.kind = "delete"
            elif value.startswith("rename from "):
                current.old = _path(value[12:])
                current.kind = "rename"
            elif value.startswith("rename to "):
                current.new = _path(value[10:])
                current.evidence = True
            elif value.startswith("Binary files ") and value.endswith(" differ"):
                current.kind = "binary"
                current.evidence = True
            elif re.fullmatch(
                r"(?:old mode|new mode|new file mode|deleted file mode) [0-7]{6}", value
            ):
                current.evidence = True
            elif re.fullmatch(
                r"similarity index \d+%|index [0-9a-f]+\.\.[0-9a-f]+(?: [0-7]{6})?",
                value,
            ):
                pass
            else:
                complete = False
    except (ValueError, UnicodeError):
        complete = False
        issues.append(ReadIssue("diff_structure", "diff 路径或结构无法完整解析"))
    except ReadFailure:
        complete = False
        issues.append(ReadIssue("diff_deadline", "diff 解析达到读取期限"))
    if remaining is not None and remaining != (0, 0):
        complete = False
    if any(not file.evidence for file in files):
        complete = False
    if not complete:
        issues.append(ReadIssue("diff_incomplete", "解析不完整；文件清单仅为部分推导"))
        # Preserve the original for safe previews even when parsing stopped early.
        return DiffView(
            (DiffFileView(None, None, "unknown", redact_text(text)),),
            False,
            tuple(issues),
        )
    return DiffView(tuple(file.view() for file in files), True, tuple(issues))
