from decimal import Decimal
from pathlib import Path

import pytest

from miniagent.models import PriceConfig, load_price_config
from miniagent.models.model import Usage
from miniagent.models.pricing import ModelPrice
from miniagent.runtime import RunUsage
from miniagent.runtime.usage import add_request_usage, calculate_request_cost


@pytest.fixture
def pricing() -> PriceConfig:
    return PriceConfig(
        models=[
            ModelPrice(
                provider="qwen",
                model="qwen3.8-flash",
                input_per_token=Decimal("0.0000004"),
                output_per_token=Decimal("0.0000012"),
            )
        ]
    )


def test_load_normalizes_unit_prices_to_token_prices(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        """
currency = "USD"
billing_unit_tokens = 1000000

[[models]]
provider = "qwen"
model = "qwen3.8-flash"
input_per_unit = "0.4"
cached_input_per_unit = "0.1"
output_per_unit = "1.2"
""",
        encoding="utf-8",
    )

    price = load_price_config(path).price_for("qwen", "qwen3.8-flash")

    assert price.input_per_token == Decimal("0.0000004")
    assert price.cached_input_per_token == Decimal("0.0000001")
    assert price.output_per_token == Decimal("0.0000012")


def test_load_preserves_missing_cached_input_price(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        """
[[models]]
provider = "qwen"
model = "input-output-only"
input_per_unit = "0.4"
output_per_unit = "1.2"
""",
        encoding="utf-8",
    )

    assert (
        load_price_config(path)
        .price_for("qwen", "input-output-only")
        .cached_input_per_token
        is None
    )


def test_duplicate_provider_model_price_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "pricing.toml"
    path.write_text(
        """
[[models]]
provider = "qwen"
model = "duplicate"
input_per_unit = "1"
output_per_unit = "1"

[[models]]
provider = "qwen"
model = "duplicate"
input_per_unit = "2"
output_per_unit = "2"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate provider/model"):
        load_price_config(path)


def test_request_cost_without_cache_price_bills_all_input(
    pricing: PriceConfig,
) -> None:
    cost = calculate_request_cost(
        provider="qwen",
        model="qwen3.8-flash",
        usage=Usage(
            input_tokens=132_935,
            cached_input_tokens=93_920,
            output_tokens=42_728,
            reasoning_output_tokens=14_382,
            total_tokens=175_663,
        ),
        pricing=pricing,
    )

    assert cost.input_cost == Decimal("0.0531740")
    assert cost.cached_input_cost == Decimal(0)
    assert cost.output_cost == Decimal("0.0512736")
    assert cost.total_cost == Decimal("0.1044476")
    assert not cost.cached_input_priced_separately


def test_request_cost_with_cache_price_splits_input_cost() -> None:
    cost = calculate_request_cost(
        provider="qwen",
        model="cache-priced",
        usage=Usage(
            input_tokens=132_935,
            cached_input_tokens=93_920,
            output_tokens=42_728,
            reasoning_output_tokens=14_382,
            total_tokens=175_663,
        ),
        pricing=PriceConfig(
            models=[
                ModelPrice(
                    provider="qwen",
                    model="cache-priced",
                    input_per_token=Decimal("0.0000004"),
                    cached_input_per_token=Decimal("0.0000001"),
                    output_per_token=Decimal("0.0000012"),
                )
            ]
        ),
    )

    assert cost.input_cost == Decimal("0.0156060")
    assert cost.cached_input_cost == Decimal("0.0093920")
    assert cost.output_cost == Decimal("0.0512736")
    assert cost.total_cost == Decimal("0.0762716")
    assert cost.cached_input_priced_separately


def test_run_usage_accumulates_request_usage_and_cost(
    pricing: PriceConfig,
) -> None:
    usages = [
        Usage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            reasoning_output_tokens=5,
            total_tokens=120,
        ),
        Usage(
            input_tokens=200,
            cached_input_tokens=50,
            output_tokens=30,
            reasoning_output_tokens=10,
            total_tokens=230,
        ),
    ]
    run_usage = RunUsage(model_requests=2, tool_calls=1)

    for usage in usages:
        run_usage = add_request_usage(
            run_usage,
            usage,
            calculate_request_cost(
                provider="qwen",
                model="qwen3.8-flash",
                usage=usage,
                pricing=pricing,
            ),
        )

    assert run_usage.model_requests == 2
    assert run_usage.tool_calls == 1
    assert run_usage.input_tokens == 300
    assert run_usage.cached_input_tokens == 90
    assert run_usage.output_tokens == 50
    assert run_usage.reasoning_output_tokens == 15
    assert run_usage.total_tokens == 350
    assert not run_usage.cached_input_priced_separately
    assert run_usage.input_cost_usd == Decimal("0.0001200")
    assert run_usage.cached_input_cost_usd == Decimal(0)
    assert run_usage.output_cost_usd == Decimal("0.0000600")
    assert run_usage.cost_usd == Decimal("0.0001800")
