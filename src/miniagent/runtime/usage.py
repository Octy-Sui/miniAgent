# File   : usage.py
# Created: 2026-09-11 16:16:05
# Author : octy Sui

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.models import PriceConfig
from miniagent.models.model import Usage
from miniagent.runtime.state import RunUsage


class RequestCost(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    currency: str
    input_cost: Decimal = Field(default=Decimal(0), ge=0)
    cached_input_cost: Decimal = Field(default=Decimal(0), ge=0)
    output_cost: Decimal = Field(default=Decimal(0), ge=0)
    total_cost: Decimal = Field(default=Decimal(0), ge=0)

    @model_validator(mode="after")
    def validate_total_cost(self) -> "RequestCost":
        expected = self.input_cost + self.cached_input_cost + self.output_cost
        if self.total_cost != expected:
            raise ValueError("Total cost must equal component costs")
        return self


def calculate_request_cost(
    *,
    provider: str,
    model: str,
    usage: Usage,
    pricing: PriceConfig,
) -> RequestCost:
    price = pricing.price_for(provider, model)
    input_cost = Decimal(usage.uncached_input_tokens) * price.input_per_token
    cached_input_cost = (
        Decimal(usage.cached_input_tokens) * price.cached_input_per_token
    )
    output_cost = Decimal(usage.output_tokens) * price.output_per_token

    return RequestCost(
        provider=provider,
        model=model,
        currency=pricing.currency,
        input_cost=input_cost,
        cached_input_cost=cached_input_cost,
        output_cost=output_cost,
        total_cost=input_cost + cached_input_cost + output_cost,
    )


def add_request_usage(
    current: RunUsage,
    usage: Usage,
    cost: RequestCost,
) -> RunUsage:
    return RunUsage(
        model_requests=current.model_requests,
        tool_calls=current.tool_calls,
        input_tokens=current.input_tokens + usage.input_tokens,
        cached_input_tokens=(current.cached_input_tokens + usage.cached_input_tokens),
        output_tokens=current.output_tokens + usage.output_tokens,
        reasoning_output_tokens=(
            current.reasoning_output_tokens + usage.reasoning_output_tokens
        ),
        total_tokens=current.total_tokens + usage.total_tokens,
        input_cost_usd=current.input_cost_usd + cost.input_cost,
        cached_input_cost_usd=(current.cached_input_cost_usd + cost.cached_input_cost),
        output_cost_usd=current.output_cost_usd + cost.output_cost,
        cost_usd=current.cost_usd + cost.total_cost,
        active_duration_ms=current.active_duration_ms,
    )
