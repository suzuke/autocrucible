"""Context assembler for dynamic prompt generation."""

from __future__ import annotations

from pathlib import Path
from typing import List

from crucible.config import Config
from crucible.results import ExperimentRecord, ResultsLog

PREAMBLE = (
    "Learn from history — if a direction worked (✓), push further. "
    "If it failed (✗) or crashed (💥), try something fundamentally different.\n"
)

_STATUS_LABELS = {"keep": "✓ KEPT", "discard": "✗ WORSE", "crash": "💥 CRASH"}


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text. Approximation: ~4 chars per token."""
    return len(text) // 4 if text else 0


# -- Crash classification patterns (order matters: first match wins) ----------

_CRASH_PATTERNS: list[tuple[str, str, str]] = [
    # (regex_pattern, diagnosis, advice)
    (r"SyntaxError|IndentationError|NameError", "Typo",
     "Fix the typo — do NOT abandon this direction."),
    (r"ImportError|ModuleNotFoundError", "Missing module",
     "This module is NOT available. ABANDON this approach and use only stdlib/built-in modules."),
    (r"TypeError|ValueError", "Logic bug",
     "Fix the bug. If this keeps recurring, rewrite the function."),
    (r"MemoryError|OOM|out of memory|Killed", "Resource limit",
     "ABANDON this direction — it exceeds available resources."),
    (r"Timed out|timed out|TIMED OUT", "Too slow",
     "ABANDON this approach — use a faster algorithm instead."),
]


def _classify_crash(stderr: str) -> tuple[str, str]:
    """Classify crash stderr into (diagnosis, advice)."""
    import re
    for pattern, diagnosis, advice in _CRASH_PATTERNS:
        if re.search(pattern, stderr, re.IGNORECASE):
            return diagnosis, advice
    return "Unknown", "Read the traceback carefully and determine the root cause."


def _strategy_hint(records: list) -> str:
    """Return a tiered strategy hint based on consecutive failures."""
    if not records:
        return (
            "Tier 1 — EXPLORE: Read ALL source code carefully. "
            "Identify the #1 performance bottleneck before making any change."
        )

    # Count consecutive non-keep records from the end
    consecutive_failures = 0
    for r in reversed(records):
        if r.status == "keep":
            break
        consecutive_failures += 1

    if consecutive_failures == 0:
        return (
            "Tier 1 — EXPLOIT: Your last change worked! "
            "Push further in the same direction — deepen the improvement."
        )
    if consecutive_failures <= 1:
        return (
            "Tier 1 — EXPLOIT: Build on what worked. "
            "Make a different but related improvement in the same direction."
        )
    if consecutive_failures <= 3:
        return (
            "Tier 2 — RE-READ: Multiple failures in a row. "
            "Stop and re-read ALL code from scratch. You may be missing something."
        )
    if consecutive_failures <= 5:
        return (
            "Tier 3 — COMBINE: Try combining two previously successful ideas "
            "into one change, or revisit a worked direction with a new twist."
        )
    return (
        "Tier 4 — RADICAL: Many consecutive failures. "
        "Make a drastically different change — completely new algorithm, "
        "different data structure, or opposite approach from everything tried."
    )


def _plateau_hint(records: list[ExperimentRecord], threshold: int) -> str | None:
    """Generate a strong prompt when metric hasn't improved for N iterations."""
    if not records:
        return None

    streak = 0
    for r in reversed(records):
        if r.status == "keep":
            break
        streak += 1

    if streak < threshold:
        return None

    recent_failures = [r.description for r in records[-streak:] if r.description]

    return (
        f"\u26a0\ufe0f The metric has NOT improved for {streak} consecutive iterations.\n"
        f"Recent attempts: {'; '.join(recent_failures[-5:])}\n"
        "You MUST try a fundamentally different approach:\n"
        "- Change the core algorithm entirely\n"
        "- Restructure the data representation\n"
        "- Challenge an assumption from earlier iterations\n"
        "- Do NOT make small tweaks to previous failed approaches"
    )


class ContextAssembler:
    """Assembles prompt sections into a complete context for the agent."""

    def __init__(self, config: Config, project_root: Path, branch_name: str) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.branch_name = branch_name
        self._errors: List[str] = []
        self._crash_info: List[str] = []
        self._last_crash_info: List[str] = []
        self._critic_notes: str | None = None
        self._prompt_breakdown: dict[str, int] | None = None

    @property
    def prompt_breakdown(self) -> dict[str, int] | None:
        """Token breakdown by section from last assemble() call, if profiling was enabled."""
        return self._prompt_breakdown

    def set_critic_notes(self, notes: str) -> None:
        """Set critic analysis notes for the next assembled prompt."""
        self._critic_notes = notes

    def add_error(self, message: str) -> None:
        """Queue an error message for the next assembled prompt."""
        self._errors.append(message)

    def add_crash_info(self, stderr_tail: str) -> None:
        """Queue crash information for the next assembled prompt."""
        self._crash_info.append(stderr_tail)

    def requeue_crash_info(self) -> None:
        """Re-queue crash info from last assemble (for skip iterations)."""
        self._crash_info.extend(self._last_crash_info)

    def _read_instructions(self) -> str:
        """Read static instructions from program.md."""
        instructions_name = self.config.agent.instructions or "program.md"
        crucible_path = self.project_root / ".crucible" / instructions_name
        root_path = self.project_root / instructions_name
        if crucible_path.exists():
            return crucible_path.read_text()
        if root_path.exists():
            return root_path.read_text()
        return ""

    def _section_instructions(self) -> str:
        """Section 1: Static instructions."""
        text = self._read_instructions()
        if text:
            return f"## Instructions\n\n{text}"
        return ""

    def _section_state(self, records: list, best, summary: dict) -> str:
        """Section 2: Current state — branch, best metric, summary, editable files."""
        lines = ["## Current State"]
        lines.append(f"\nBranch: {self.branch_name}")

        if best is not None:
            direction_hint = (
                "lower is better" if self.config.metric.direction == "minimize"
                else "higher is better"
            )
            if best.status == "baseline":
                lines.append(
                    f"**Baseline from previous run: {best.metric_value}** "
                    f"(Goal: {self.config.metric.direction} — {direction_hint}) "
                    f"— you must beat this score."
                )
            else:
                lines.append(
                    f"**Best {self.config.metric.name} so far: {best.metric_value}** "
                    f"(Goal: {self.config.metric.direction} — {direction_hint})"
                )

        if summary["total"] > 0:
            lines.append(
                f"Experiments: {summary['total']} total, "
                f"{summary['kept']} kept, "
                f"{summary['discarded']} discarded, "
                f"{summary['crashed']} crashed"
            )

        if summary["total"] > 0 and summary["kept"] == 0:
            lines.append("⚠ No improvements yet — try a fundamentally different approach")

        # Convergence warning: let agent know auto-stop is approaching
        cw = self.config.constraints.convergence_window
        if cw is not None and summary["total"] >= cw // 2:
            streak = ResultsLog.plateau_streak(records)
            if streak >= cw * 3 // 4:
                lines.append(
                    f"⚠️ Convergence warning: {streak}/{cw} iterations without improvement. "
                    "Make a breakthrough or experiment will auto-stop."
                )

        editable = ", ".join(self.config.files.editable)
        lines.append(f"Editable files: {editable}")

        if self.config.files.hidden:
            hidden = ", ".join(self.config.files.hidden)
            lines.append(
                f"Hidden files (exist but you CANNOT read, create, or modify them): {hidden}"
            )

        if self.config.files.artifacts:
            artifact_list = ", ".join(self.config.files.artifacts)
            lines.append(
                f"Persistent directories (survive across iterations, not version-controlled): {artifact_list}"
            )
            lines.append(
                "Files in these directories are NOT affected by revert. "
                "Use them to store model weights, training data, or other artifacts that should persist."
            )

        if self.config.constraints.allow_install:
            lines.append(
                "Package installation: ENABLED — you can add packages by editing requirements.txt"
            )
            req = self.project_root / "requirements.txt"
            if req.exists():
                deps = [l.strip() for l in req.read_text().splitlines() if l.strip() and not l.startswith("#")]
                if deps:
                    lines.append(f"Available packages: {', '.join(deps)} (+ Python stdlib)")
                else:
                    lines.append("Available packages: Python stdlib only")
            else:
                lines.append("Available packages: Python stdlib only (edit requirements.txt to add more)")

        return "\n".join(lines)

    def _section_history(self, records: list[ExperimentRecord]) -> str:
        """Section 3: Experiment history table with actionable lessons."""
        cw = self.config.agent.context_window
        if not cw.include_history:
            return ""

        recent = records[-cw.history_limit:]
        if not recent:
            return (
                "## Experiment History\n\n"
                "No experiments yet. Read ALL the code carefully, then make "
                "ONE high-impact improvement targeting the main bottleneck.\n\n"
                f"**Strategy:** {_strategy_hint([])}"
            )

        lines = ["## Experiment History"]

        # Kept iterations: one-line summary only (code already reflects these)
        kept_records = [r for r in recent if r.status == "keep"]
        if kept_records:
            lines.append("\n**Improvements:**")
            for r in kept_records:
                lines.append(f"- ✓ metric={r.metric_value}")

        # Failed iterations: recent 5 with full diff, older ones one-line
        failed_records = [r for r in recent if r.status in ("discard", "crash")]
        if failed_records:
            lines.append("\n**Failed — do NOT repeat these changes:**")
            older = failed_records[:-5]
            recent_failed = failed_records[-5:]
            for r in older:
                label = _STATUS_LABELS.get(r.status, r.status)
                lines.append(f"- {label} (metric={r.metric_value})")
            for r in recent_failed:
                label = _STATUS_LABELS.get(r.status, r.status)
                lines.append(f"\n{label} (metric={r.metric_value})")
                if r.diff_text:
                    lines.append(f"```diff\n{r.diff_text}\n```")
                else:
                    lines.append(r.description)

        # Metric trend for kept records
        kept_values = [r.metric_value for r in records if r.status == "keep"]
        if len(kept_values) >= 2:
            first, last = kept_values[0], kept_values[-1]
            if first != 0:
                pct = ((last - first) / abs(first)) * 100
                lines.append(f"\n**Metric trend: {first} → {last} ({pct:+.1f}%)**")

        # Tiered strategy based on consecutive failures
        lines.append("")
        lines.append(f"**Strategy:** {_strategy_hint(records)}")

        return "\n".join(lines)

    def _section_errors(self) -> str:
        """Render queued errors and crash info with strong warnings."""
        parts = []
        if self._errors:
            parts.append("## Errors (MUST fix or avoid)\n")
            for msg in self._errors:
                parts.append(f"- {msg}")
            parts.append(
                "\n**Do NOT repeat the approach that caused these errors. "
                "Try a different strategy.**"
            )
        if self._crash_info:
            parts.append(
                "\n## Crash Info (CRITICAL — your last change broke the code)\n"
            )
            for info in self._crash_info:
                diagnosis, advice = _classify_crash(info)
                parts.append(f"**Diagnosis: {diagnosis}** — {advice}\n")
                parts.append(f"```\n{info}\n```")
            parts.append(
                "\n**Your previous edit caused a crash. Read the file "
                "first to understand the current state.**"
            )
        return "\n".join(parts) if parts else ""

    def _section_critic(self) -> str:
        """Render critic agent analysis if available."""
        if not self._critic_notes:
            return ""
        return (
            "## Critic Analysis (from independent reviewer)\n\n"
            f"{self._critic_notes}\n\n"
            "Consider this analysis but use your own judgment."
        )

    def _section_directive(self) -> str:
        """Section 4: Action directive with mandatory workflow."""
        editable = ", ".join(self.config.files.editable)
        rules = (
            "**Rules:**\n"
            "- NEVER repeat a failed/crashed approach, even with small variations\n"
            "- ONE change per iteration — don't combine multiple ideas\n"
            "- A crash scores zero — correctness first\n"
            "- Simplicity test: if a change adds >50 lines of complexity "
            "for <1% expected improvement, it is NOT worth it. "
            "Prefer clean, targeted changes.\n"
            "- Do NOT output full file contents. Use targeted edits."
        )
        if self.config.constraints.allow_install:
            rules += "\n- To use a new package: add it to requirements.txt AND import it in your code"
        explain_step = (
            "4. **EXPLAIN** — One short line (<120 chars, no markdown): what you changed and why."
        )
        if self.config.agent.failure_analysis:
            explain_step = (
                "4. **EXPLAIN** — Output in this exact format:\n"
                "HYPOTHESIS: <why you think this change will improve the metric>\n"
                "CHANGE: <one line summary of what you changed>\n"
                "RISK: <what could go wrong>"
            )
        return (
            "## Your Task\n\n"
            "**Workflow (STRICT ORDER):**\n\n"
            "1. **READ** — Use Glob to find files, then Read ALL relevant code. "
            "NEVER edit without reading first.\n"
            "2. **THINK** — What is the #1 bottleneck? Study experiment history: "
            "✓ means push further, ✗/💥 means NEVER retry that direction.\n"
            "3. **EDIT** — Make ONE bold, high-impact change to: " + editable + ". "
            "Ensure syntactic correctness and preserve the interface.\n"
            + explain_step + "\n\n"
            + rules
        )

    def assemble_with_files(
        self, log: ResultsLog, workspace: Path, editable_files: list[str],
        *, profile: bool = False,
    ) -> str:
        """Assemble prompt with file contents inlined (for agents without read tools)."""
        base = self.assemble(log, profile=profile)
        parts = [base, "\n\n---\n\n## Editable File Contents\n"]
        file_text = ""
        for fname in editable_files:
            fpath = workspace / fname
            try:
                content = fpath.read_text()
            except (FileNotFoundError, OSError):
                continue
            block = f"\n### {fname}\n```\n{content}\n```\n"
            parts.append(block)
            file_text += block
        if profile and self._prompt_breakdown is not None:
            file_tokens = _estimate_tokens(file_text)
            self._prompt_breakdown["file_inline"] = file_tokens
            self._prompt_breakdown["total"] = self._prompt_breakdown.get("total", 0) + file_tokens
        return "".join(parts)

    def _section_cross_beam_history(self, beam_summaries: list[dict]) -> str:
        """Compact view of other beams' attempts (read-only context for current beam).

        beam_summaries: list of {beam_id: int, best: float | None, tried: list[ExperimentRecord]}
        """
        if not beam_summaries:
            return ""

        lines = ["## Other Beams (read-only — do NOT repeat approaches already tried there)"]
        for summary in beam_summaries:
            bid = summary["beam_id"]
            best = summary.get("best")
            tried = summary.get("tried", [])

            parts = []
            for r in tried[-8:]:
                symbol = {"keep": "✓", "crash": "💥", "discard": "✗"}.get(r.status, "?")
                parts.append(f"{symbol} {r.description}")

            best_str = f"{best}" if best is not None else "N/A"
            tried_str = " | ".join(parts) if parts else "no attempts yet"
            lines.append(f"beam-{bid}  best={best_str}  tried: {tried_str}")

        return "\n".join(lines)

    def assemble(
        self, log: ResultsLog, beam_summaries: list[dict] | None = None,
        *, profile: bool = False,
    ) -> str:
        """Assemble all sections into a complete prompt."""
        if beam_summaries is None:
            beam_summaries = getattr(self, "_beam_summaries", None) or []

        records = log.read_all()
        direction = self.config.metric.direction
        candidates = [r for r in records if r.status in ("keep", "baseline")]
        if candidates:
            best = min(candidates, key=lambda r: r.metric_value) if direction == "minimize" else max(candidates, key=lambda r: r.metric_value)
        else:
            best = None
        # Filter out baseline for summary and history
        real_records = [r for r in records if r.status != "baseline"]
        summary = {
            "total": len(real_records),
            "kept": sum(1 for r in real_records if r.status == "keep"),
            "discarded": sum(1 for r in real_records if r.status == "discard"),
            "crashed": sum(1 for r in real_records if r.status == "crash"),
        }

        plateau = _plateau_hint(real_records, self.config.search.plateau_threshold)

        sections_map = {
            "instructions": self._section_instructions(),
            "state": self._section_state(real_records, best, summary),
            "cross_beam_history": self._section_cross_beam_history(beam_summaries),
            "history": self._section_history(real_records),
            "plateau_hint": plateau or "",
            "critic": self._section_critic(),
            "errors": self._section_errors(),
            "directive": self._section_directive(),
        }

        if profile:
            self._prompt_breakdown = {
                name: _estimate_tokens(text)
                for name, text in sections_map.items() if text
            }
            self._prompt_breakdown["preamble"] = _estimate_tokens(PREAMBLE)
            self._prompt_breakdown["total"] = sum(self._prompt_breakdown.values())
        else:
            self._prompt_breakdown = None

        prompt = "\n\n---\n\n".join(s for s in sections_map.values() if s)

        # Save crash info before clearing (for requeue on skip iterations)
        self._last_crash_info = list(self._crash_info)
        # Clear transient context after assembly
        self._errors.clear()
        self._crash_info.clear()
        self._critic_notes = None

        return PREAMBLE + "\n---\n\n" + prompt
