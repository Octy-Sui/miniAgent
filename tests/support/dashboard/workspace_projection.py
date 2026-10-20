from miniagent.dashboard.reads import ReadBudget


def budget() -> ReadBudget:
    return ReadBudget(5, monotonic=lambda: 0)
