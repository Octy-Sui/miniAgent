from math import ceil
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from miniagent.tools.contracts import ToolOutputLimits

Delay = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ModelRetryConfig(BaseModel):
    max_consecutive_failures: int = Field(default=5, ge=1)
    timeout_seconds: float = Field(default=900, gt=0, le=86400, allow_inf_nan=False)
    rate_limit_delays: tuple[Delay, ...] = Field(
        default=(10, 30, 60, 120), min_length=1
    )
    server_delays: tuple[Delay, ...] = Field(default=(1, 2, 5, 10), min_length=1)
    gateway_delays: tuple[Delay, ...] = Field(default=(5, 10, 20, 40), min_length=1)
    connection_delays: tuple[Delay, ...] = Field(default=(1, 2, 5, 10), min_length=1)
    jitter_ratio: float = Field(default=0.2, ge=0, le=1, allow_inf_nan=False)


class RuntimeConfig(BaseModel):
    # Absent in historical frozen definitions; preserve their digest and retry behavior.
    model_retry: ModelRetryConfig | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    max_steps: int = Field(default=250, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    approval_timeout_seconds: float = Field(default=300.0, gt=0)
    tool_output_limits: ToolOutputLimits = Field(default_factory=ToolOutputLimits)

    context_window_tokens: int = Field(default=32768, gt=0)
    compaction_trigger_ratio: float = Field(default=0.70, gt=0, lt=1)
    compaction_target_ratio: float = Field(default=0.50, gt=0, lt=1)
    recent_steps_to_keep: int = Field(default=3, ge=1)
    compaction_timeout_seconds: float = Field(default=60, gt=0)
    compaction_max_retries: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_ratios(self) -> "RuntimeConfig":
        if self.compaction_target_ratio >= self.compaction_trigger_ratio:
            raise ValueError("compaction target must be below trigger")
        return self

    def validate_output_limit(self, output: int | None) -> None:
        if output is None or output <= 0:
            raise ValueError("model output limit must be a positive integer")
        budget = (
            self.context_window_tokens
            - output
            - max(1024, ceil(0.05 * self.context_window_tokens))
        )
        if budget <= 0:
            raise ValueError("business input budget must be positive")
        if min(2048, int(0.15 * budget)) < 256:
            raise ValueError("summary reserve must be at least 256 tokens")
