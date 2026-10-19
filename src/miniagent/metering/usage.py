from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniagent.metering.pricing import PriceConfig
from miniagent.models.protocol import ModelUsage


class RunUsage(BaseModel):
    model_requests: int = Field(default=0, ge=0)
    unconfirmed_model_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_input_priced_separately: bool = False
    input_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    cached_input_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    output_cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    cost_usd: Decimal = Field(default=Decimal(0), ge=0)
    active_duration_ms: int = Field(default=0, ge=0)


class RequestCost(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    currency: str
    cached_input_priced_separately: bool = False
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
    *, provider: str, model: str, usage: ModelUsage, pricing: PriceConfig
) -> RequestCost:
    price = pricing.price_for(provider, model)
    cached_input_priced_separately = price.cached_input_per_token is not None
    if price.cached_input_per_token is None:
        input_cost = Decimal(usage.input_tokens) * price.input_per_token
        cached_input_cost = Decimal(0)
    else:
        input_cost = Decimal(usage.uncached_input_tokens) * price.input_per_token
        cached_input_cost = (
            Decimal(usage.cached_input_tokens) * price.cached_input_per_token
        )
    output_cost = Decimal(usage.output_tokens) * price.output_per_token
    return RequestCost(
        provider=provider,
        model=model,
        currency=pricing.currency,
        cached_input_priced_separately=cached_input_priced_separately,
        input_cost=input_cost,
        cached_input_cost=cached_input_cost,
        output_cost=output_cost,
        total_cost=input_cost + cached_input_cost + output_cost,
    )


def add_request_usage(
    current: RunUsage,
    usage: ModelUsage,
    cost: RequestCost | None = None,
) -> RunUsage:
    return RunUsage(
        model_requests=current.model_requests,
        unconfirmed_model_requests=current.unconfirmed_model_requests,
        tool_calls=current.tool_calls,
        input_tokens=current.input_tokens + usage.input_tokens,
        cached_input_tokens=current.cached_input_tokens + usage.cached_input_tokens,
        output_tokens=current.output_tokens + usage.output_tokens,
        reasoning_output_tokens=(
            current.reasoning_output_tokens + usage.reasoning_output_tokens
        ),
        total_tokens=current.total_tokens + usage.total_tokens,
        cached_input_priced_separately=(
            current.cached_input_priced_separately
            or (cost.cached_input_priced_separately if cost is not None else False)
        ),
        input_cost_usd=current.input_cost_usd
        + (cost.input_cost if cost is not None else Decimal(0)),
        cached_input_cost_usd=current.cached_input_cost_usd
        + (cost.cached_input_cost if cost is not None else Decimal(0)),
        output_cost_usd=current.output_cost_usd
        + (cost.output_cost if cost is not None else Decimal(0)),
        cost_usd=current.cost_usd
        + (cost.total_cost if cost is not None else Decimal(0)),
        active_duration_ms=current.active_duration_ms,
    )
