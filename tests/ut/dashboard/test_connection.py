import pytest

from miniagent.dashboard.reads import ReadBudget, ReadFailure


def test_budget_reservation_does_not_consume_rejected_bytes() -> None:
    budget = ReadBudget(5, remaining_bytes=3, monotonic=lambda: 0)
    assert not budget.reserve(4)
    assert budget.remaining_bytes == 3
    assert budget.reserve(3)
    assert budget.remaining_bytes == 0
    with pytest.raises(ValueError, match="negative"):
        budget.reserve(-1)


@pytest.mark.parametrize(
    ("now", "expired"), [(4.999, False), (5.0, True), (5.001, True)]
)
def test_deadline_boundary(now: float, expired: bool) -> None:
    budget = ReadBudget(5, monotonic=lambda: now)
    if expired:
        with pytest.raises(ReadFailure, match="read_deadline"):
            budget.check()
    else:
        budget.check()
