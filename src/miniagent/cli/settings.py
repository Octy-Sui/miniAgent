from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunLocationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    run_database_path: Path = Path.home() / ".miniagent" / "runs.sqlite3"

    @field_validator("run_database_path")
    @classmethod
    def absolute_database_path(cls, value: Path) -> Path:
        return value.expanduser().resolve()


class Settings(RunLocationSettings):
    openai_api_key: str
    openai_base_url: str
    model_limits_file: Path = Path("config/model_limits.toml")
    model_pricing_file: Path = Path("config/model_pricing.toml")
    workspace_backend: Literal["local", "docker"] = "local"
    local_workspace_root: Path = Path.home() / ".miniagent" / "workspaces"
    artifact_root: Path = Path.home() / ".miniagent" / "artifacts"
    docker_image: str | None = None
    swebench_data_root: Path = (
        Path.home() / ".miniagent/evaluation/swebench-verified-25-20261010"
    )
    swebench_work_root: Path = Path.home() / ".miniagent/evaluation/runs"
    swebench_executable: str = "swebench"
    evaluation_docker_executable: str = "docker"
    candidate_submission_timeout_seconds: float = Field(
        default=60.0, gt=0, allow_inf_nan=False
    )
    patch_export_cleanup_timeout_seconds: float = Field(
        default=5.0, gt=0, allow_inf_nan=False
    )
    swebench_test_timeout_seconds: int = Field(default=1800, gt=0)
    swebench_process_timeout_seconds: float = Field(
        default=2400.0, gt=0, allow_inf_nan=False
    )
    swebench_cleanup_timeout_seconds: float = Field(
        default=30.0, gt=0, allow_inf_nan=False
    )
    swebench_artifact_timeout_seconds: float = Field(
        default=30.0, gt=0, allow_inf_nan=False
    )
    artifact_collection_timeout_seconds: float = Field(
        default=30.0, gt=0, allow_inf_nan=False
    )
    swebench_allow_image_pull: bool = False

    @model_validator(mode="after")
    def validate_evaluation_budgets(self) -> "Settings":
        if self.swebench_process_timeout_seconds <= self.swebench_test_timeout_seconds:
            raise ValueError("Harness process timeout must exceed test timeout")
        if (
            self.candidate_submission_timeout_seconds
            <= self.patch_export_cleanup_timeout_seconds
        ):
            raise ValueError("Submission timeout must exceed export cleanup reserve")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )
