import logging
import os
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.resources import files

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from miniagent.dashboard.artifacts import ArtifactReader, OpenFd
from miniagent.dashboard.config import DashboardSettings
from miniagent.dashboard.cursors import CursorSigner, utc_now
from miniagent.dashboard.pages import PageDependencies
from miniagent.dashboard.presentation import render_page
from miniagent.dashboard.queries.connection import ConnectionFactory, validate_database
from miniagent.dashboard.reads import ReadFailure
from miniagent.dashboard.routes import router
from miniagent.dashboard.workers import HeavyWorkPool

_LOG = logging.getLogger(__name__)
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


def error_response(status: int, *, error_id: str | None = None) -> HTMLResponse:
    messages = {
        403: "仅允许本机同源访问。",
        404: "记录不存在。",
        405: "仅支持只读 GET / HEAD 请求。",
        409: "记录或内容完整性不一致。",
        422: "参数或导航令牌无效，请返回页面重新打开。",
        503: "读取暂不可用，请稍后重试。",
    }
    return HTMLResponse(
        render_page(
            "error.html",
            {
                "status": status,
                "message": messages.get(status, "读取失败，请使用错误 ID 查询诊断。"),
                "error_id": error_id,
            },
        ),
        status_code=status,
    )


class LocalBoundary:
    def __init__(self, app: ASGIApp, *, port: int) -> None:
        self.app = app
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            self.hosts |= {"127.0.0.1", "localhost"}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope["headers"]:
            headers.setdefault(key.lower(), []).append(value)
        host_values = headers.get(b"host", [])
        host = host_values[0].decode("latin1") if len(host_values) == 1 else ""
        origins = headers.get(b"origin", [])
        origin = f"http://{host}".encode("ascii", errors="replace")
        valid = (
            host in self.hosts
            and scope["scheme"] == "http"
            and (not origins or origins == [origin])
            and b"cross-site" not in headers.get(b"sec-fetch-site", [])
        )

        generations = headers.get(b"x-dashboard-generation", [])
        generation = generations[0] if len(generations) == 1 else b""
        valid_generation = not generations or (
            len(generations) == 1
            and 1 <= len(generation) <= 10
            and generation.isdigit()
            and generation == str(int(generation)).encode()
            and int(generation) <= 2147483647
        )

        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    *(
                        (key.lower().encode(), value.encode())
                        for key, value in _HEADERS.items()
                    ),
                ]
                if generation and valid_generation:
                    message["headers"].append((b"x-dashboard-generation", generation))
            elif message["type"] == "http.response.body" and scope["method"] == "HEAD":
                message["body"] = b""
            await send(message)

        if not valid:
            await error_response(403)(scope, receive, secure_send)
            return
        if not valid_generation:
            await error_response(422)(scope, receive, secure_send)
            return
        try:
            await self.app(scope, receive, secure_send)
        except ReadFailure as exc:
            await error_response(exc.status_code)(scope, receive, secure_send)
        except Exception as exc:  # noqa: BLE001 — translate unknown failures at the HTTP boundary
            error_id = secrets.token_hex(8)
            # Never log request strings, exception values, paths, or untrusted data.
            _LOG.error(
                "dashboard read failed error_id=%s category=%s",
                error_id,
                type(exc).__name__,
            )
            await error_response(500, error_id=error_id)(scope, receive, secure_send)


def create_app(
    settings: DashboardSettings,
    *,
    connect: ConnectionFactory = sqlite3.connect,
    open_fd: OpenFd = os.open,
    now: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    work_pool: HeavyWorkPool | None = None,
) -> FastAPI:
    pool = work_pool if work_pool is not None else HeavyWorkPool()
    signer = CursorSigner(secrets.token_bytes(32), now=now)
    deps = PageDependencies(
        settings,
        signer,
        ArtifactReader(settings.artifacts, open_fd=open_fd),
        connect,
        now,
        monotonic,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await pool.run(
                lambda: validate_database(settings.database, connect=connect)
            )
            yield
        finally:
            await pool.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.pool, app.state.signer, app.state.deps = pool, signer, deps
    app.add_middleware(LocalBoundary, port=settings.port)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return error_response(422)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(exc.status_code)

    app.include_router(router)
    app.mount(
        "/static",
        StaticFiles(directory=str(files("miniagent.dashboard") / "static")),
        name="static",
    )
    return app
