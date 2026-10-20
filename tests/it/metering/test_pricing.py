from decimal import Decimal
from pathlib import Path

import pytest

from miniagent.metering.pricing import load_price_config


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
