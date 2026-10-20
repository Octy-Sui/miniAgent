from decimal import Decimal

import pytest

from miniagent.dashboard.projections.run import format_usd


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("0.0000005"), "0.000000"), (Decimal("0.0000015"), "0.000002")],
)
def test_currency_rounding(value: Decimal, expected: str) -> None:
    assert format_usd(value) == expected
