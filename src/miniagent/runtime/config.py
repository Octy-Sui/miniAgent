from math import ceil

from pydantic import BaseModel, Field, model_validator

from miniagent.tools.contracts import ToolOutputLimits


class RuntimeConfig(BaseModel):
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
