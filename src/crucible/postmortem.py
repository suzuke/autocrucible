"""Postmortem analysis for experiment runs."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from crucible.results import ResultsLog

logger = logging.getLogger(__name__)


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

    def __init__(self, results_path: Path, direction: str) -> None:
        self.direction = direction
        self.log = ResultsLog(results_path)

    @classmethod
    def from_path(cls, results_path: Path, direction: str) -> "PostmortemAnalyzer":
        """Create analyzer pointing to a specific results file."""
        return cls(results_path=results_path, direction=direction)

    def analyze(self) -> PostmortemReport:
        records = self.log.read_all()
        return self._build_report(records)

    def analyze_from_string(self, tsv_content: str) -> PostmortemReport:
        """Analyze from raw TSV content (e.g., read from a specific branch)."""
        records = ResultsLog.read_from_string(tsv_content)
        return self._build_report(records)

    def _build_report(self, records: list) -> PostmortemReport:
        report = PostmortemReport()

        report.total = len(records)
        report.kept = sum(1 for r in records if r.status == "keep")
        report.discarded = sum(1 for r in records if r.status == "discard")
        report.crashed = sum(1 for r in records if r.status == "crash")

        kept = [r for r in records if r.status == "keep"]
        if kept:
            if self.direction == "minimize":
                best = min(kept, key=lambda r: r.metric_value)
            else:
                best = max(kept, key=lambda r: r.metric_value)
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

    def add_ai_insights(self, report: PostmortemReport) -> None:
        """Call Claude to generate AI insights and set report.ai_insights."""
        prompt = self._build_insights_prompt(report)
        report.ai_insights = _call_claude_for_insights(prompt)

    def _build_insights_prompt(self, report: PostmortemReport) -> str:
        """Assemble a prompt from report data for Claude analysis."""
        lines: list[str] = []
        lines.append("Analyze this optimization experiment run:")
        lines.append("")
        lines.append(f"Direction: {self.direction}")
        lines.append(f"Total iterations: {report.total}")
        lines.append(f"Kept: {report.kept}, Discarded: {report.discarded}, Crashed: {report.crashed}")
        if report.best_metric is not None:
            lines.append(f"Best metric: {report.best_metric} (commit {report.best_commit})")
        lines.append("")

        lines.append("Iteration history:")
        for t in report.trend:
            metric = t["metric"] if t["metric"] is not None else "N/A"
            desc = t.get("description", "") or ""
            lines.append(f"  iter {t['iteration']}: {metric} ({t['status']}) — {desc}")
        lines.append("")

        if report.failure_streaks:
            lines.append("Failure streaks:")
            for s in report.failure_streaks:
                end = s["start"] + s["length"] - 1
                lines.append(f"  iter {s['start']}-{end}: {s['length']} consecutive failures")
            lines.append("")

        lines.append(
            "Provide 3-5 numbered insights about: turning points, failure patterns, "
            "plateaus, and suggested next directions."
        )
        return "\n".join(lines)

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


async def _call_claude_async(prompt: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt=(
                "You are an experiment analyst. Provide concise, actionable "
                "insights about optimization experiment results."
            ),
            permission_mode="bypassPermissions",
            allowed_tools=[],
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or "(no insights generated)"
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved


def _call_claude_for_insights(prompt: str) -> str:
    """Call Claude Agent SDK to get AI insights. Returns text."""
    try:
        return asyncio.run(_call_claude_async(prompt))
    except Exception as e:
        logger.warning(f"AI insights failed: {e}")
        return f"(AI analysis unavailable: {e})"


def render_text(report: PostmortemReport) -> str:
    """Render a PostmortemReport as a human-readable terminal string."""
    if report.total == 0:
        return "No iterations recorded."

    lines: list[str] = []

    # Summary
    lines.append("## Summary")
    best_str = f"{report.best_metric} ({report.best_commit})" if report.best_metric is not None else "N/A"
    lines.append(f"  Best: {best_str}")
    kept_pct = int(100 * report.kept / report.total) if report.total else 0
    lines.append(
        f"  Kept: {report.kept}/{report.total} ({kept_pct}%)  |  "
        f"Discarded: {report.discarded}  |  Crashed: {report.crashed}"
    )
    lines.append("")

    # Metric Trend
    if report.trend:
        lines.append("## Metric Trend")
        max_metric = max(
            (t["metric"] for t in report.trend if t["metric"] is not None and t["metric"] > 0),
            default=1.0,
        )
        bar_width = 20
        for t in report.trend:
            metric = t["metric"] if t["metric"] is not None else 0.0
            filled = round(bar_width * metric / max_metric) if max_metric > 0 else 0
            filled = max(0, min(bar_width, filled))
            empty = bar_width - filled
            bar = "\u2588" * filled + "\u2591" * empty

            # Star marker for the best commit with keep status
            is_best = (
                t["commit"] == report.best_commit
                and t["status"] == "keep"
            )
            star = " \u2605 " if is_best else "   "

            # Truncate description to 40 chars
            desc = t.get("description", "") or ""
            if len(desc) > 40:
                desc = desc[:39] + "\u2026"

            lines.append(
                f"  iter {t['iteration']:>3} {bar} {metric:>5}  "
                f"  {t['status']:<9}{star}{desc}"
            )
        lines.append("")

    # Failure Streaks
    if report.failure_streaks:
        lines.append("## Failure Streaks")
        for s in report.failure_streaks:
            end = s["start"] + s["length"] - 1
            lines.append(
                f"  iter {s['start']}-{end}: {s['length']} consecutive failures"
            )
        lines.append("")

    # AI Insights
    if report.ai_insights:
        lines.append("## Key Insights")
        lines.append(f"  {report.ai_insights}")
        lines.append("")

    return "\n".join(lines).rstrip()

