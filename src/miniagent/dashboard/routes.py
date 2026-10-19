from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from miniagent.dashboard.pages import (
    render_artifact,
    render_events,
    render_run,
    render_runs,
    render_snapshot,
    render_workspace,
)
from miniagent.dashboard.parameters import (
    ArtifactParams,
    SnapshotParams,
    WorkspaceParams,
    event_parameters,
    run_parameters,
)
from miniagent.dashboard.reads import ReadFailure

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


@router.api_route("/runs/{run_id}", methods=["GET", "HEAD"])
async def run_detail(request: Request, run_id: UUID) -> Response:
    params = event_parameters(list(request.query_params.multi_items()))
    page = await request.app.state.pool.run(
        lambda: render_run(
            request.app.state.deps,
            run_id,
            params,
            fragment=request.headers.get("hx-request") == "true",
        )
    )
    return HTMLResponse(page.html, status_code=page.status_code)


@router.api_route("/runs/{run_id}/events", methods=["GET", "HEAD"])
async def events(request: Request, run_id: UUID) -> Response:
    params = event_parameters(list(request.query_params.multi_items()))
    page = await request.app.state.pool.run(
        lambda: render_events(
            request.app.state.deps,
            run_id,
            params,
            fragment=request.headers.get("hx-request") == "true",
        )
    )
    return HTMLResponse(page.html, status_code=page.status_code)


@router.api_route("/runs/{run_id}/snapshots/{version}", methods=["GET", "HEAD"])
async def snapshot(request: Request, run_id: UUID, version: int) -> Response:
    pairs = list(request.query_params.multi_items())
    if len({key for key, _ in pairs}) != len(pairs):
        raise ReadFailure(422, "parameters_invalid")
    try:
        params = SnapshotParams.model_validate(dict(pairs))
    except ValueError as exc:
        raise ReadFailure(422, "parameters_invalid") from exc
    page = await request.app.state.pool.run(
        lambda: render_snapshot(request.app.state.deps, run_id, version, params)
    )
    return HTMLResponse(page.html, status_code=page.status_code)


def _parameters[T: BaseModel](request: Request, model: type[T]) -> T:
    pairs = list(request.query_params.multi_items())
    if len({key for key, _ in pairs}) != len(pairs):
        raise ReadFailure(422, "parameters_invalid")
    try:
        return model.model_validate(dict(pairs))
    except ValueError as exc:
        raise ReadFailure(422, "parameters_invalid") from exc


@router.api_route("/runs/{run_id}/workspace", methods=["GET", "HEAD"])
async def workspace(request: Request, run_id: UUID) -> Response:
    params = _parameters(request, WorkspaceParams)
    page = await request.app.state.pool.run(
        lambda: render_workspace(
            request.app.state.deps,
            run_id,
            params,
            fragment=request.headers.get("hx-request") == "true",
        )
    )
    return HTMLResponse(page.html, status_code=page.status_code)


@router.api_route("/runs/{run_id}/artifacts/{artifact_id}", methods=["GET", "HEAD"])
async def artifact(request: Request, run_id: UUID, artifact_id: UUID) -> Response:
    params = _parameters(request, ArtifactParams)
    page = await request.app.state.pool.run(
        lambda: render_artifact(request.app.state.deps, run_id, artifact_id, params)
    )
    return HTMLResponse(page.html, status_code=page.status_code)
