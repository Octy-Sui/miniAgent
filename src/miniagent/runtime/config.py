from pydantic import BaseModel, Field

from miniagent.tools.contracts import ToolOutputLimits


class RuntimeConfig(BaseModel):
    max_steps: int = Field(default=50, ge=1)
    max_consecutive_failures: int = Field(default=3, ge=1)
    approval_timeout_seconds: float = Field(default=300.0, gt=0)
    tool_output_limits: ToolOutputLimits = Field(default_factory=ToolOutputLimits)
