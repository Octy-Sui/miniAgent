from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str
    openai_base_url: str
    model_pricing_file: Path = Path("config/model_pricing.toml")
    workspace_backend: Literal["local", "docker"] = "local"
    local_workspace_root: Path = Path.home() / ".miniagent" / "workspaces"
    artifact_root: Path = Path.home() / ".miniagent" / "artifacts"
    run_database_path: Path = Path.home() / ".miniagent" / "runs.sqlite3"
    docker_image: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )
