from fastapi import APIRouter, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from miniagent.dashboard.pages import render_runs
from miniagent.dashboard.parameters import run_parameters

router = APIRouter()


@router.api_route("/", methods=["GET", "HEAD"])
async def home() -> Response:
    return RedirectResponse("/runs", status_code=307)


@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> Response:
    return JSONResponse({"status": "ok"})


@router.api_route("/runs", methods=["GET", "HEAD"])
async def runs(request: Request) -> Response:
    params = run_parameters(list(request.query_params.multi_items()))
    page = await request.app.state.pool.run(
        lambda: render_runs(
            request.app.state.deps,
            params,
            fragment=request.headers.get("hx-request") == "true",
        )
    )
    return HTMLResponse(page.html, status_code=page.status_code)
