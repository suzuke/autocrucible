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


class ContextAssembler:
    """Assembles prompt sections into a complete context for the agent."""

    def __init__(self, config: Config, project_root: Path, branch_name: str) -> None:
        self.config = config
        self.project_root = Path(project_root)
        self.branch_name = branch_name
        self._errors: List[str] = []
        self._crash_info: List[str] = []

    def add_error(self, message: str) -> None:
        """Queue an error message for the next assembled prompt."""
        self._errors.append(message)

    def add_crash_info(self, stderr_tail: str) -> None:
        """Queue crash information for the next assembled prompt."""
        self._crash_info.append(stderr_tail)

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

        editable = ", ".join(self.config.files.editable)
        lines.append(f"Editable files: {editable}")

        if self.config.files.hidden:
            hidden = ", ".join(self.config.files.hidden)
            lines.append(
                f"Hidden files (exist but you CANNOT read, create, or modify them): {hidden}"
            )

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
                "ONE high-impact improvement targeting the main bottleneck."
            )

        lines = ["## Experiment History"]
        lines.append("")
        lines.append("| # | Metric | Status | Description |")
        lines.append("|---|--------|--------|-------------|")
        for i, r in enumerate(recent, 1):
            label = _STATUS_LABELS.get(r.status, r.status)
            lines.append(
                f"| {i} | {r.metric_value} | {label} | {r.description} |"
            )

        # Metric trend for kept records
        kept_values = [r.metric_value for r in records if r.status == "keep"]
        if len(kept_values) >= 2:
            first, last = kept_values[0], kept_values[-1]
            if first != 0:
                pct = ((last - first) / abs(first)) * 100
                lines.append(f"\n**Metric trend: {first} → {last} ({pct:+.1f}%)**")

        # Actionable lessons
        kept = [r.description for r in records if r.status == "keep"][-5:]
        discarded = [r.description for r in records if r.status == "discard"][-5:]
        crashed = [r.description for r in records if r.status == "crash"][-5:]

        if kept or discarded or crashed:
            lines.append("")
            lines.append("### Key Lessons")
            if kept:
                lines.append(
                    f"**✓ WORKED — build on these:** {'; '.join(kept)}"
                )
            if discarded:
                lines.append(
                    f"**✗ FAILED — do NOT repeat:** {'; '.join(discarded)}"
                )
            if crashed:
                lines.append(
                    f"**💥 CRASHED — avoid entirely:** {'; '.join(crashed)}"
                )
            lines.append("")
            if len(records) <= 1:
                lines.append(
                    "**Strategy:** Early in optimization. Focus on understanding "
                    "the code deeply and finding the biggest performance bottleneck."
                )
            else:
                lines.append(
                    "**Strategy:** Build on what worked. Make a different but "
                    "related improvement in the same direction as successful changes."
                )

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
                parts.append(f"```\n{info}\n```")
            parts.append(
                "\n**Your previous edit caused a crash. You MUST revert your "
                "approach and try something completely different. Read the file "
                "first to understand the current state.**"
            )
        return "\n".join(parts) if parts else ""

    def _section_directive(self) -> str:
        """Section 4: Action directive with mandatory workflow."""
        editable = ", ".join(self.config.files.editable)
        return (
            "## Your Task\n\n"
            "**Workflow (STRICT ORDER):**\n\n"
            "1. **READ** — Use Glob to find files, then Read ALL relevant code. "
            "NEVER edit without reading first.\n"
            "2. **THINK** — What is the #1 bottleneck? Study experiment history: "
            "✓ means push further, ✗/💥 means NEVER retry that direction.\n"
            "3. **EDIT** — Make ONE bold, high-impact change to: " + editable + ". "
            "Ensure syntactic correctness and preserve the interface.\n"
            "4. **EXPLAIN** — One line: what you changed and expected improvement.\n\n"
            "**Rules:**\n"
            "- NEVER repeat a failed/crashed approach, even with small variations\n"
            "- ONE change per iteration — don't combine multiple ideas\n"
            "- A crash scores zero — correctness first\n"
            "- Do NOT output full file contents. Use targeted edits."
        )

    def assemble(self, log: ResultsLog) -> str:
        """Assemble all sections into a complete prompt."""
        records = log.read_all()
        direction = self.config.metric.direction
        kept = [r for r in records if r.status == "keep"]
        if kept:
            best = min(kept, key=lambda r: r.metric_value) if direction == "minimize" else max(kept, key=lambda r: r.metric_value)
        else:
            best = None
        summary = {
            "total": len(records),
            "kept": len(kept),
            "discarded": sum(1 for r in records if r.status == "discard"),
            "crashed": sum(1 for r in records if r.status == "crash"),
        }

        sections = [
            self._section_instructions(),
            self._section_state(records, best, summary),
            self._section_history(records),
            self._section_errors(),
            self._section_directive(),
        ]
        prompt = "\n\n---\n\n".join(s for s in sections if s)

        # Clear transient context after assembly
        self._errors.clear()
        self._crash_info.clear()

        return PREAMBLE + "\n---\n\n" + prompt
