from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from miniagent.agents import AgentSpec
from miniagent.metering.pricing import PriceConfig
from miniagent.runtime.config import RuntimeConfig
from miniagent.tools.contracts import ToolDefinition


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TrajectoryModelLimits(_Contract):
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class TrajectoryBenchmark(_Contract):
    instance_id: str = Field(min_length=1)
    task_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    tasks_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    harness_version: str | None = None


class TrajectoryDefinition(_Contract):
    task: str
    agent: AgentSpec
    tools: tuple[ToolDefinition, ...] = ()
    budgets: RuntimeConfig
    model_limits: TrajectoryModelLimits | None = None
    pricing: PriceConfig | None = None
    provider_endpoint_identity: str | None = None
    workspace_kind: str | None = None
    workspace_id: str | None = None
    base_revision: str | None = None
    evaluator: str | None = None
    benchmark: TrajectoryBenchmark | None = None

    @field_validator("provider_endpoint_identity")
    @classmethod
    def nonsecret_endpoint(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "provider_endpoint_identity must be a nonsecret HTTP endpoint"
                )
        return value


class TrajectoryBoundary(_Contract):
    through_sequence: int = Field(ge=0)
    status: str
    terminal: bool
    snapshot_version: int = Field(ge=0)
    kind: Literal["terminal", "suspended", "abnormal"]


class CoverageIssue(_Contract):
    code: str
    sequence: int | None = None
    attempt_id: UUID | None = None
    reason: str


class TrajectoryHeader(_Contract):
    schema_version: Literal[1] = 1
    kind: Literal["miniagent.trajectory"] = "miniagent.trajectory"
    run_id: UUID
    trace_id: str
    producer_version: str
    exported_at: datetime
    boundary: TrajectoryBoundary
    definition: TrajectoryDefinition
    summary: dict[str, JsonValue]


class TrajectoryExportError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
