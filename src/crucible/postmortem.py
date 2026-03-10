"""Postmortem analysis for experiment runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from crucible.results import ResultsLog


@dataclass
class PostmortemReport:
    total: int = 0
    kept: int = 0
    discarded: int = 0
    crashed: int = 0
    best_metric: Optional[float] = None
    best_commit: Optional[str] = None
    best_description: Optional[str] = None
    trend: list[dict] = field(default_factory=list)
    failure_streaks: list[dict] = field(default_factory=list)
    ai_insights: Optional[str] = None


class PostmortemAnalyzer:
    """Analyzes experiment results from a workspace."""

    def __init__(self, workspace: Path, direction: str) -> None:
        self.workspace = Path(workspace)
        self.direction = direction
        self.log = ResultsLog(self.workspace / "results.tsv")

    def analyze(self) -> PostmortemReport:
        records = self.log.read_all()
        report = PostmortemReport()

        report.total = len(records)
        report.kept = sum(1 for r in records if r.status == "keep")
        report.discarded = sum(1 for r in records if r.status == "discard")
        report.crashed = sum(1 for r in records if r.status == "crash")

        best = self.log.best(self.direction)
        if best:
            report.best_metric = best.metric_value
            report.best_commit = best.commit
            report.best_description = best.description

        report.trend = [
            {
                "iteration": i + 1,
                "metric": r.metric_value,
                "status": r.status,
                "description": r.description,
                "commit": r.commit,
            }
            for i, r in enumerate(records)
        ]

        report.failure_streaks = self._find_failure_streaks(records)

        return report

    @staticmethod
    def _find_failure_streaks(records) -> list[dict]:
        streaks: list[dict] = []
        streak_start: int | None = None
        streak_len = 0
        for i, r in enumerate(records):
            if r.status != "keep":
                if streak_start is None:
                    streak_start = i + 1  # 1-indexed
                streak_len += 1
            else:
                if streak_len >= 2:
                    streaks.append({"start": streak_start, "length": streak_len})
                streak_start = None
                streak_len = 0
        if streak_len >= 2:
            streaks.append({"start": streak_start, "length": streak_len})
        return streaks
