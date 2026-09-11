import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from miniagent.models import PriceConfig, load_price_config
from miniagent.models.model import Usage
from miniagent.models.pricing import ModelPrice
from miniagent.runtime import RunUsage
from miniagent.runtime.usage import add_request_usage, calculate_request_cost


def price_config() -> PriceConfig:
    return PriceConfig(
        models=[
            ModelPrice(
                provider="qwen",
                model="qwen3.8-flash",
                input_per_token=Decimal("0.0000004"),
                cached_input_per_token=Decimal("0.0000001"),
                output_per_token=Decimal("0.0000012"),
            )
        ]
    )


class PriceConfigTests(unittest.TestCase):
    def test_load_normalizes_unit_prices_to_token_prices(self) -> None:
        content = """
currency = "USD"
billing_unit_tokens = 1000000

[[models]]
provider = "qwen"
model = "qwen3.8-flash"
input_per_unit = "0.4"
cached_input_per_unit = "0.1"
output_per_unit = "1.2"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.toml"
            path.write_text(content, encoding="utf-8")

            pricing = load_price_config(path)

        price = pricing.price_for("qwen", "qwen3.8-flash")
        self.assertEqual(price.input_per_token, Decimal("0.0000004"))
        self.assertEqual(price.cached_input_per_token, Decimal("0.0000001"))
        self.assertEqual(price.output_per_token, Decimal("0.0000012"))

    def test_duplicate_provider_model_price_is_rejected(self) -> None:
        content = """
[[models]]
provider = "qwen"
model = "duplicate"
input_per_unit = "1"
cached_input_per_unit = "1"
output_per_unit = "1"

[[models]]
provider = "qwen"
model = "duplicate"
input_per_unit = "2"
cached_input_per_unit = "2"
output_per_unit = "2"
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.toml"
            path.write_text(content, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Duplicate provider/model"):
                load_price_config(path)


class UsageCostTests(unittest.TestCase):
    def test_request_cost_bills_all_output_tokens(self) -> None:
        usage = Usage(
            input_tokens=132_935,
            cached_input_tokens=93_920,
            output_tokens=42_728,
            reasoning_output_tokens=14_382,
            total_tokens=175_663,
        )

        cost = calculate_request_cost(
            provider="qwen",
            model="qwen3.8-flash",
            usage=usage,
            pricing=price_config(),
        )

        self.assertEqual(cost.input_cost, Decimal("0.0156060"))
        self.assertEqual(cost.cached_input_cost, Decimal("0.0093920"))
        self.assertEqual(cost.output_cost, Decimal("0.0512736"))
        self.assertEqual(cost.total_cost, Decimal("0.0762716"))

    def test_run_usage_accumulates_request_usage_and_cost(self) -> None:
        pricing = price_config()
        first_usage = Usage(
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            reasoning_output_tokens=5,
            total_tokens=120,
        )
        second_usage = Usage(
            input_tokens=200,
            cached_input_tokens=50,
            output_tokens=30,
            reasoning_output_tokens=10,
            total_tokens=230,
        )
        run_usage = RunUsage(model_requests=2, tool_calls=1)

        for usage in (first_usage, second_usage):
            cost = calculate_request_cost(
                provider="qwen",
                model="qwen3.8-flash",
                usage=usage,
                pricing=pricing,
            )
            run_usage = add_request_usage(run_usage, usage, cost)

        self.assertEqual(run_usage.model_requests, 2)
        self.assertEqual(run_usage.tool_calls, 1)
        self.assertEqual(run_usage.input_tokens, 300)
        self.assertEqual(run_usage.cached_input_tokens, 90)
        self.assertEqual(run_usage.output_tokens, 50)
        self.assertEqual(run_usage.reasoning_output_tokens, 15)
        self.assertEqual(run_usage.total_tokens, 350)
        self.assertEqual(run_usage.input_cost_usd, Decimal("0.0000840"))
        self.assertEqual(run_usage.cached_input_cost_usd, Decimal("0.0000090"))
        self.assertEqual(run_usage.output_cost_usd, Decimal("0.0000600"))
        self.assertEqual(run_usage.cost_usd, Decimal("0.0001530"))


if __name__ == "__main__":
    unittest.main()
