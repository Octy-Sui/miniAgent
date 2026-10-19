from pathlib import Path
from typing import Annotated

import typer

from miniagent.dashboard.config import DashboardSettings

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
_DEFAULTS = DashboardSettings()


@app.command()
def serve(
    database: Annotated[
        Path, typer.Option(help="SQLite Run 数据库路径")
    ] = _DEFAULTS.database,
    artifacts: Annotated[
        Path, typer.Option(help="已保存 Artifact 根目录")
    ] = _DEFAULTS.artifacts,
    port: Annotated[int, typer.Option(min=1, max=65535, help="本机 HTTP 端口")] = 8080,
) -> None:
    from importlib.util import find_spec

    if find_spec("fastapi") is None or find_spec("uvicorn") is None:
        typer.echo("Dashboard 需要可选依赖：uv sync --extra dashboard", err=True)
        typer.echo("或安装 miniagent[dashboard]。", err=True)
        raise typer.Exit(code=1)

    import sqlite3

    import uvicorn

    from miniagent.dashboard.app import create_app
    from miniagent.dashboard.queries.connection import validate_database
    from miniagent.dashboard.reads import ReadFailure

    settings = DashboardSettings(database=database, artifacts=artifacts, port=port)
    try:
        validate_database(settings.database)
    except (ReadFailure, sqlite3.DatabaseError):
        typer.echo("数据库不可用或存储版本不支持；请检查数据库后重试。", err=True)
        raise typer.Exit(code=1) from None
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=settings.port,
        workers=1,
        access_log=False,
    )


def entry() -> None:
    app()


if __name__ == "__main__":
    entry()
