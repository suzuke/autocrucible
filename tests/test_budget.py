"""Tests for budget tracking and control."""

from crucible.config import BudgetConfig
from crucible.budget import BudgetGuard
from crucible.results import UsageInfo


class TestBudgetGuardNoConfig:
    def test_always_ok(self):
        guard = BudgetGuard(None)
        assert guard.check(None) == "ok"
        assert guard.check(UsageInfo(estimated_cost_usd=100.0)) == "ok"

    def test_percent_used_zero(self):
        guard = BudgetGuard(None)
        assert guard.percent_used == 0.0


class TestAccumulate:
    def test_with_valid_usage(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.accumulate(UsageInfo(input_tokens=100, output_tokens=50, estimated_cost_usd=0.5))
        assert guard.total_cost == 0.5
        assert guard.iteration_count == 1

    def test_multiple_accumulations(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.accumulate(UsageInfo(estimated_cost_usd=0.3))
        guard.accumulate(UsageInfo(estimated_cost_usd=0.7))
        assert guard.total_cost == 1.0
        assert guard.iteration_count == 2

    def test_with_none_usage(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.accumulate(None)
        assert guard.total_cost == 0.0
        assert guard.iteration_count == 1

    def test_with_none_cost_field(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.accumulate(UsageInfo(input_tokens=100, output_tokens=50, estimated_cost_usd=None))
        assert guard.total_cost == 0.0
        assert guard.iteration_count == 1


class TestCheckExceeded:
    def test_total_exceeds_max(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=1.0))
        guard.total_cost = 1.5
        assert guard.check(None) == "exceeded"

    def test_per_iter_exceeds_max(self):
        guard = BudgetGuard(BudgetConfig(max_cost_per_iter_usd=0.5))
        usage = UsageInfo(estimated_cost_usd=0.8)
        assert guard.check(usage) == "exceeded"

    def test_per_iter_within_limit(self):
        guard = BudgetGuard(BudgetConfig(max_cost_per_iter_usd=1.0))
        usage = UsageInfo(estimated_cost_usd=0.5)
        assert guard.check(usage) == "ok"


class TestCheckWarning:
    def test_at_warn_threshold(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0, warn_at_percent=80))
        guard.total_cost = 8.0
        assert guard.check(None) == "warning"

    def test_above_warn_threshold(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0, warn_at_percent=80))
        guard.total_cost = 9.0
        assert guard.check(None) == "warning"

    def test_below_warn_threshold(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0, warn_at_percent=80))
        guard.total_cost = 7.0
        assert guard.check(None) == "ok"


class TestCheckOk:
    def test_within_all_limits(self):
        guard = BudgetGuard(BudgetConfig(
            max_cost_usd=10.0,
            max_cost_per_iter_usd=2.0,
            warn_at_percent=80,
        ))
        guard.total_cost = 3.0
        usage = UsageInfo(estimated_cost_usd=1.0)
        assert guard.check(usage) == "ok"


class TestPercentUsed:
    def test_zero_cost(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        assert guard.percent_used == 0.0

    def test_half_used(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.total_cost = 5.0
        assert guard.percent_used == 50.0

    def test_fully_used(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=10.0))
        guard.total_cost = 10.0
        assert guard.percent_used == 100.0

    def test_no_max_cost(self):
        guard = BudgetGuard(BudgetConfig(max_cost_usd=None))
        guard.total_cost = 5.0
        assert guard.percent_used == 0.0
