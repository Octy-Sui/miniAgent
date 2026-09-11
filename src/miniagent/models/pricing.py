# File   : pricing.py
# Created: 2026-09-11 16:21:24
# Author : octy Sui

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelUnitPrice(BaseModel):
    provider: str
    model: str
    input_per_unit: Decimal = Field(ge=0)
    cached_input_per_unit: Decimal = Field(ge=0)
    output_per_unit: Decimal = Field(ge=0)


class PriceConfigInput(BaseModel):
    currency: Literal["USD"] = "USD"
    billing_unit_tokens: int = Field(default=1_000_000, ge=1)
    models: list[ModelUnitPrice]

    @model_validator(mode="after")
    def validate_unique_models(self) -> "PriceConfigInput":
        targets = [(price.provider, price.model) for price in self.models]
        if len(targets) != len(set(targets)):
            raise ValueError("Duplicate provider/model pricing entries")
        return self


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    input_per_token: Decimal = Field(ge=0)
    cached_input_per_token: Decimal = Field(ge=0)
    output_per_token: Decimal = Field(ge=0)


class PriceConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    currency: Literal["USD"] = "USD"
    models: list[ModelPrice]

    @classmethod
    def from_input(cls, config: PriceConfigInput) -> "PriceConfig":
        unit = Decimal(config.billing_unit_tokens)
        return cls(
            currency=config.currency,
            models=[
                ModelPrice(
                    provider=price.provider,
                    model=price.model,
                    input_per_token=price.input_per_unit / unit,
                    cached_input_per_token=price.cached_input_per_unit / unit,
                    output_per_token=price.output_per_unit / unit,
                )
                for price in config.models
            ],
        )

    def price_for(self, provider: str, model: str) -> ModelPrice:
        for price in self.models:
            if price.provider == provider and price.model == model:
                return price
        raise ValueError(
            f"No pricing configured for provider={provider!r}, model={model!r}"
        )


def load_price_config(path: Path) -> PriceConfig:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid pricing configuration {path}: {exc}") from exc

    return PriceConfig.from_input(PriceConfigInput.model_validate(data))
