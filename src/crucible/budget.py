"""Budget tracking and control for experiment runs."""

from __future__ import annotations

from dataclasses import dataclass

from crucible.results import UsageInfo


@dataclass
class BudgetConfig:
    max_cost_usd: float | None = None
    max_cost_per_iter_usd: float | None = None
    warn_at_percent: int = 80


class BudgetGuard:
    """Tracks cumulative cost and enforces budget limits."""

    def __init__(self, config: BudgetConfig | None) -> None:
        self.config = config
        self.total_cost: float = 0.0
        self.iteration_count: int = 0

    @property
    def percent_used(self) -> float:
        if not self.config or not self.config.max_cost_usd:
            return 0.0
        return (self.total_cost / self.config.max_cost_usd) * 100

    def accumulate(self, usage: UsageInfo | None) -> None:
        """Add cost from one iteration."""
        if usage and usage.estimated_cost_usd:
            self.total_cost += usage.estimated_cost_usd
        self.iteration_count += 1

    def check(self, usage: UsageInfo | None) -> str:
        """Check budget limits. Returns 'ok', 'warning', or 'exceeded'."""
        if not self.config:
            return "ok"

        # Per-iteration check
        if (self.config.max_cost_per_iter_usd
                and usage and usage.estimated_cost_usd
                and usage.estimated_cost_usd > self.config.max_cost_per_iter_usd):
            return "exceeded"

        # Total budget check
        if self.config.max_cost_usd and self.total_cost > self.config.max_cost_usd:
            return "exceeded"

        # Warning threshold
        if (self.config.max_cost_usd and self.config.warn_at_percent
                and self.percent_used >= self.config.warn_at_percent):
            return "warning"

        return "ok"
