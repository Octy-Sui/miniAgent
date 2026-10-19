from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DashboardSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database: Path = Path.home() / ".miniagent/runs.sqlite3"
    artifacts: Path = Path.home() / ".miniagent/artifacts"
    port: int = Field(default=8080, ge=1, le=65535)


@dataclass(frozen=True)
class ReadLimits:
    busy_seconds: float = 1.0
    read_seconds: float = 5.0
    state_bytes: int = 20 * 1024 * 1024
    snapshot_bytes: int = 20 * 1024 * 1024
    event_bytes: int = 1024 * 1024
    recovery_bytes: int = 1024 * 1024
    manifest_bytes: int = 1024 * 1024
    structured_bytes: int = 64 * 1024 * 1024
    task_bytes: int = 128 * 1024
    scalar_bytes: int = 4096
    artifact_bytes: int = 20 * 1024 * 1024
    block_bytes: int = 64 * 1024
    preview_bytes: int = 256 * 1024
    preview_lines: int = 2000
    metadata_page: int = 100
    locator_radius: int = 20
    parent_depth: int = 64
    scopes: int = 1000
    compaction_versions: int = 32
    json_depth: int = 64
    heavy_tasks: int = 2
    cursor_bytes: int = 4096
    cursor_seconds: int = 1800
    run_page: int = 25
    event_page: int = 100
    event_page_max: int = 200
    task_search_chars: int = 200


LIMITS = ReadLimits()
