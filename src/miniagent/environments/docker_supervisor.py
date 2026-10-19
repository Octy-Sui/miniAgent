from __future__ import annotations

import base64
import json
import math
import os
import select
import signal
import struct
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from miniagent.environments.contracts import Command

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 262144


def encode_guest_frame(
    attempt_id: UUID, execution_id: UUID, kind: str, body: dict[str, object]
) -> bytes:
    content = json.dumps(
        {
            "version": 1,
            "attempt_id": str(attempt_id),
            "execution_id": str(execution_id),
            "type": kind,
            "body": body,
        },
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(content) > MAX_FRAME_BYTES:
        raise ValueError("Guest frame exceeds protocol limit")
    return struct.pack(">I", len(content)) + content


def encode_command(
    command: Command,
    *,
    attempt_id: UUID,
    execution_id: UUID,
    timeout_seconds: float,
    stdin_bytes: bytes | None = None,
) -> bytes:
    configuration = command.model_dump(mode="json")
    if stdin_bytes is not None:
        configuration["stdin_base64"] = base64.b64encode(stdin_bytes).decode()
    content = json.dumps(configuration).encode()
    frames = [
        encode_guest_frame(
            attempt_id, execution_id, "configure", {"timeout_seconds": timeout_seconds}
        )
    ]
    for offset in range(0, len(content), 32768):
        frames.append(
            encode_guest_frame(
                attempt_id,
                execution_id,
                "configuration_part",
                {
                    "content": base64.b64encode(
                        content[offset : offset + 32768]
                    ).decode()
                },
            )
        )
    frames.append(encode_guest_frame(attempt_id, execution_id, "configuration_end", {}))
    return b"".join(frames)


def _read_exact(fd: int, length: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < length:
        remaining = deadline - monotonic()
        if remaining <= 0 or not select.select([fd], [], [], max(0, remaining))[0]:
            raise TimeoutError("Guest control deadline exhausted")
        chunk = os.read(fd, length - len(result))
        if not chunk:
            raise EOFError("Guest issuer channel closed")
        result.extend(chunk)
    return bytes(result)


def _read_frame(deadline: float) -> dict:
    size = struct.unpack(">I", _read_exact(0, 4, deadline))[0]
    if size > MAX_FRAME_BYTES:
        raise ValueError("Guest control frame exceeds limit")
    frame = json.loads(_read_exact(0, size, deadline))
    if (
        not isinstance(frame, dict)
        or set(frame) != {"version", "attempt_id", "execution_id", "type", "body"}
        or type(frame["version"]) is not int
        or frame["version"] != 1
    ):
        raise ValueError("Invalid guest protocol envelope")
    return frame


def _write_frame(
    attempt_id: UUID,
    execution_id: UUID,
    kind: str,
    body: dict[str, object],
    deadline: float,
) -> None:
    content = memoryview(encode_guest_frame(attempt_id, execution_id, kind, body))
    while content:
        remaining = deadline - monotonic()
        if remaining <= 0 or not select.select([], [1], [], max(0, remaining))[1]:
            raise TimeoutError("Guest output deadline exhausted")
        written = os.write(1, content)
        content = content[written:]


def _same_scope(frame: dict, attempt_id: UUID, execution_id: UUID) -> bool:
    return frame["attempt_id"] == str(attempt_id) and frame["execution_id"] == str(
        execution_id
    )


def _stat(pid: int) -> tuple[str, int, int, int]:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[0], int(fields[2]), int(fields[3]), int(fields[19])


def _identity(pid: int) -> dict:
    _, pgid, sid, ticks = _stat(pid)
    if pgid != pid or sid != pid:
        raise ValueError("Guest command session identity unavailable")
    return {
        "pid": pid,
        "pgid": pgid,
        "sid": sid,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "start_ticks": ticks,
    }


def _group_active(identity: dict) -> bool:
    if (
        Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        != identity["boot_id"]
    ):
        raise ValueError("Guest boot identity changed")
    _, pgid, sid, ticks = _stat(identity["pid"])
    if (pgid, sid, ticks) != (
        identity["pgid"],
        identity["sid"],
        identity["start_ticks"],
    ):
        raise ValueError("Guest command birth identity changed")
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            state, group, session, birth = _stat(int(item.name))
        except FileNotFoundError:
            continue
        if group == identity["pgid"] and session == identity["sid"]:
            if birth < identity["start_ticks"]:
                raise ValueError("Guest group membership is ambiguous")
            if state != "Z":
                return True
    return False


def inspect_guest_group(identity: dict) -> bool:
    if (
        Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        != identity["boot_id"]
    ):
        raise ValueError("Guest boot identity changed")
    try:
        _, pgid, sid, ticks = _stat(identity["pid"])
    except FileNotFoundError:
        pass
    else:
        if (pgid, sid, ticks) != (
            identity["pgid"],
            identity["sid"],
            identity["start_ticks"],
        ):
            raise ValueError("Guest command birth identity changed")
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            state, group, session, birth = _stat(int(item.name))
        except FileNotFoundError:
            continue
        if group == identity["pgid"] and session == identity["sid"]:
            if birth < identity["start_ticks"]:
                raise ValueError("Guest group membership is ambiguous")
            if state != "Z":
                return True
    return False


def _child(
    command: dict, gate: int, ready: int, input_fd: int, output_fd: int, error_fd: int
) -> None:
    try:
        os.setsid()
        os.dup2(input_fd, 0)
        os.dup2(output_fd, 1)
        os.dup2(error_fd, 2)
        os.write(ready, b"ready")
        os.close(ready)
        if os.read(gate, 1) != b"1":
            os._exit(125)
        os.close(gate)
        os.chdir(command["cwd"])
        os.execvpe(
            command["argv"][0],
            command["argv"],
            dict(os.environ, **command["environment"]),
        )
    except BaseException:  # noqa: BLE001 -- fork children must exit without unwinding supervisor code.
        os._exit(127)


def _kill_owned(pid: int, identity: dict | None, deadline: float) -> bool:
    if identity is not None:
        _, group, session, birth = _stat(pid)
        if (group, session, birth) != (
            identity["pgid"],
            identity["sid"],
            identity["start_ticks"],
        ):
            return False
        os.killpg(group, signal.SIGKILL)
        while monotonic() < deadline:
            if not _group_active(identity):
                return True
            select.select([], [], [], min(0.01, max(0, deadline - monotonic())))
        return False
    os.kill(pid, signal.SIGKILL)
    return False


def run_supervisor() -> int:
    os.set_blocking(1, False)
    opening = _read_frame(monotonic() + 10.0)
    if opening["type"] != "configure":
        raise ValueError("Guest requires configuration")
    attempt_id, execution_id = (
        UUID(opening["attempt_id"]),
        UUID(opening["execution_id"]),
    )
    budget = opening["body"]["timeout_seconds"]
    if type(budget) not in (int, float) or not budget > 0 or not math.isfinite(budget):
        raise ValueError("Invalid guest deadline")
    deadline = monotonic() + budget
    source = bytearray()
    while True:
        frame = _read_frame(deadline)
        if not _same_scope(frame, attempt_id, execution_id):
            raise ValueError("Guest configuration scope mismatch")
        if frame["type"] == "configuration_end":
            break
        if frame["type"] != "configuration_part":
            raise ValueError("Unexpected guest configuration frame")
        source.extend(base64.b64decode(frame["body"]["content"], validate=True))
    command = json.loads(source)
    if (
        not isinstance(command, dict)
        or not isinstance(command.get("argv"), list)
        or not command["argv"]
        or not isinstance(command.get("environment"), dict)
        or not isinstance(command.get("cwd"), str)
    ):
        raise ValueError("Invalid guest command")
    gate_read, gate_write = os.pipe()
    ready_read, ready_write = os.pipe()
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    error_read, error_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(gate_write)
        os.close(ready_read)
        _child(command, gate_read, ready_write, input_read, output_write, error_write)
        os._exit(127)
    for fd in (gate_read, ready_write, input_read, output_write, error_write):
        os.close(fd)
    identity = None
    reaped = False
    remaining_fds = {gate_write, ready_read, input_write, output_read, error_read}
    try:
        if _read_exact(ready_read, 5, deadline) != b"ready":
            raise ValueError("Guest child gate did not initialize")
        identity = _identity(pid)
        _write_frame(
            attempt_id, execution_id, "guest_identified", {"guest": identity}, deadline
        )
        permit = _read_frame(deadline)
        if permit["type"] != "permit_command" or not _same_scope(
            permit, attempt_id, execution_id
        ):
            raise ValueError("Guest command permission mismatch")
        os.write(gate_write, b"1")
        os.close(gate_write)
        remaining_fds.remove(gate_write)
        input_bytes = memoryview(
            base64.b64decode(command["stdin_base64"], validate=True)
            if "stdin_base64" in command
            else (command.get("stdin") or "").encode()
        )
        os.set_blocking(input_write, False)
        streams = {output_read: 1, error_read: 2}
        for fd in streams:
            os.set_blocking(fd, False)
        business_deadline = deadline - min(0.5, budget / 4)
        while True:
            status = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            timed_out = monotonic() >= business_deadline
            if timed_out:
                if not _kill_owned(pid, identity, deadline):
                    raise ValueError("Guest timeout group end unconfirmed")
                _write_frame(
                    attempt_id,
                    execution_id,
                    "completed",
                    {
                        "status": "timeout",
                        "exit_code": None,
                        "group_ended": True,
                        "issuer_ended": True,
                    },
                    deadline,
                )
                return 0
            if status is not None:
                if _group_active(identity):
                    raise ValueError("Guest command leader exited with a live group")
                if not streams:
                    code = (
                        status.si_status
                        if status.si_code == os.CLD_EXITED
                        else 128 + status.si_status
                    )
                    _write_frame(
                        attempt_id,
                        execution_id,
                        "completed",
                        {
                            "status": "success" if code == 0 else "error",
                            "exit_code": code,
                            "group_ended": True,
                            "issuer_ended": True,
                        },
                        deadline,
                    )
                    return 0
            if not input_bytes and input_write in remaining_fds:
                os.close(input_write)
                remaining_fds.remove(input_write)
            readable, writable, _ = select.select(
                [0, *streams],
                [input_write] if input_bytes else [],
                [],
                min(0.01, max(0, business_deadline - monotonic())),
            )
            if 0 in readable:
                if not os.read(0, 1):
                    raise EOFError("Guest issuer ended")
                raise ValueError("Unexpected guest permission after command start")
            if writable:
                try:
                    written = os.write(input_write, input_bytes[:32768])
                    input_bytes = input_bytes[written:]
                except BrokenPipeError:
                    input_bytes = memoryview(b"")
            for fd in readable:
                if fd == 0:
                    continue
                data = os.read(fd, 32768)
                if not data:
                    del streams[fd]
                    os.close(fd)
                    remaining_fds.remove(fd)
                else:
                    _write_frame(
                        attempt_id,
                        execution_id,
                        "data",
                        {
                            "stream": streams[fd],
                            "content": base64.b64encode(data).decode(),
                        },
                        deadline,
                    )
    except (EOFError, TimeoutError, OSError, ValueError):
        _kill_owned(pid, identity, deadline)
        _write_frame(
            attempt_id,
            execution_id,
            "error",
            {"code": "GUEST_EXECUTION_UNCONFIRMED"},
            deadline,
        )
        return 1
    finally:
        if not reaped:
            _kill_owned(pid, identity, deadline)
            result, _ = os.waitpid(pid, os.WNOHANG)
            reaped = result == pid
        for fd in remaining_fds:
            os.close(fd)


def main() -> None:
    try:
        result = run_supervisor()
    except (EOFError, TimeoutError, OSError, ValueError):
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
