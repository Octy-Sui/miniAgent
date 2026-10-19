import base64
import socket
import struct
import sys
from pathlib import Path
from time import monotonic
from typing import Literal
from uuid import UUID

import docker
from docker.errors import DockerException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from miniagent.environments.contracts import Command

MAX_FRAME_BYTES = 262144
PROTOCOL_VERSION = 1


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    attempt_id: UUID
    execution_id: UUID
    type: str
    body: dict[str, JsonValue] = Field(default_factory=dict)


class HelperRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["execute", "inspect"]
    endpoint: str
    cert_path: Path | None
    tls_verify: bool
    container_id: str
    command: Command | None
    exec_id: str | None = None
    deadline: float = Field(allow_inf_nan=False)


def encode_frame(frame: Frame) -> bytes:
    content = frame.model_dump_json().encode()
    if len(content) > MAX_FRAME_BYTES:
        raise ValueError("Helper frame exceeds protocol limit")
    return struct.pack(">I", len(content)) + content


def _read_exact(channel: socket.socket, count: int, deadline: float) -> bytes:
    result = bytearray()
    while len(result) < count:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Helper deadline exhausted")
        channel.settimeout(min(10.0, remaining))
        chunk = channel.recv(count - len(result))
        if not chunk:
            if result:
                raise ValueError("Truncated helper or Docker frame")
            raise EOFError("Helper parent channel closed")
        result.extend(chunk)
    return bytes(result)


def receive_frame(channel: socket.socket, deadline: float) -> Frame:
    length = struct.unpack(">I", _read_exact(channel, 4, deadline))[0]
    if length > MAX_FRAME_BYTES:
        raise ValueError("Helper frame exceeds protocol limit")
    return Frame.model_validate_json(_read_exact(channel, length, deadline))


def _send(channel: socket.socket, frame: Frame, deadline: float) -> None:
    if monotonic() >= deadline:
        raise TimeoutError("Helper deadline exhausted")
    channel.settimeout(max(0.001, min(10.0, deadline - monotonic())))
    channel.sendall(encode_frame(frame))


def _permit(channel: socket.socket, request: Frame, kind: str, deadline: float) -> None:
    permit = receive_frame(channel, deadline)
    if (
        permit.type != kind
        or permit.attempt_id != request.attempt_id
        or permit.execution_id != request.execution_id
    ):
        raise ValueError("Helper permission scope mismatch")


def _client(request: HelperRequest) -> docker.APIClient:
    tls = False
    if request.tls_verify:
        if request.cert_path is None:
            raise ValueError("Verified TLS requires certificate references")
        tls = docker.tls.TLSConfig(
            client_cert=(
                str(request.cert_path / "cert.pem"),
                str(request.cert_path / "key.pem"),
            ),
            ca_cert=str(request.cert_path / "ca.pem"),
            verify=True,
        )
    remaining = request.deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Helper deadline exhausted")
    return docker.APIClient(
        base_url=request.endpoint, tls=tls, version="1.45", timeout=min(10.0, remaining)
    )


def serve(channel: socket.socket) -> None:
    opening = receive_frame(channel, monotonic() + 10.0)
    if opening.type != "request":
        raise ValueError("Helper requires an opening request")
    request = HelperRequest.model_validate(opening.body)

    def send(kind: str, body: dict[str, JsonValue]) -> None:
        _send(
            channel,
            Frame(
                attempt_id=opening.attempt_id,
                execution_id=opening.execution_id,
                type=kind,
                body=body,
            ),
            request.deadline,
        )

    if request.mode == "inspect":
        _permit(channel, opening, "permit_inspect", request.deadline)
        with _client(request) as client:
            result = client.exec_inspect(request.exec_id)
            send(
                "inspection",
                {"running": result["Running"], "exit_code": result.get("ExitCode")},
            )
        return
    _permit(channel, opening, "permit_create", request.deadline)
    command = request.command
    if command is None:
        raise ValueError("Execution request requires command")
    with _client(request) as client:
        created = client.exec_create(
            request.container_id,
            command.argv,
            stdin=command.stdin is not None,
            stdout=True,
            stderr=True,
            workdir=command.cwd,
            environment=command.environment,
        )
        exec_id = created["Id"]
        send("exec_created", {"exec_id": exec_id})
        _permit(channel, opening, "permit_start", request.deadline)
        if monotonic() >= request.deadline:
            raise TimeoutError("Helper deadline exhausted before RPC")
        client.timeout = min(10.0, request.deadline - monotonic())
        stream = client.exec_start(exec_id, socket=True)
        raw = getattr(stream, "_sock", stream)
        try:
            raw.settimeout(min(10.0, max(0.001, request.deadline - monotonic())))
            send("exec_started", {"exec_id": exec_id})
            if command.stdin is not None:
                raw.sendall(command.stdin.encode())
                raw.shutdown(socket.SHUT_WR)
            while True:
                try:
                    header = _read_exact(raw, 8, request.deadline)
                except EOFError:
                    break
                number, length = struct.unpack(">BxxxI", header)
                if number not in (1, 2):
                    raise ValueError("Unsupported Docker output stream")
                while length:
                    chunk = _read_exact(raw, min(length, 32768), request.deadline)
                    length -= len(chunk)
                    send(
                        "data",
                        {"stream": number, "content": base64.b64encode(chunk).decode()},
                    )
            if monotonic() >= request.deadline:
                raise TimeoutError("Helper deadline exhausted before RPC")
            client.timeout = min(10.0, request.deadline - monotonic())
            result = client.exec_inspect(exec_id)
            if result["Running"] or not isinstance(result.get("ExitCode"), int):
                raise ValueError("Docker command completion is unconfirmed")
            send("finished", {"exit_code": result["ExitCode"]})
        finally:
            stream.close()


def main() -> None:
    channel = socket.socket(fileno=int(sys.argv[1]))
    try:
        serve(channel)
    except (EOFError, TimeoutError, OSError, ValueError, DockerException):
        raise SystemExit(1) from None
    finally:
        channel.close()


if __name__ == "__main__":
    main()
