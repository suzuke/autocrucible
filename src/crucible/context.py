"""Context assembler for dynamic prompt generation."""

from __future__ import annotations

from pathlib import Path
from typing import List

from crucible.config import Config
from crucible.results import ResultsLog


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
        # Try .crucible/<instructions> first, then project root
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

    def _section_state(self, log: ResultsLog) -> str:
        """Section 2: Current state — branch, best metric, summary, editable files."""
        lines = ["## Current State"]
        lines.append(f"\nBranch: {self.branch_name}")

        # Best metric
        best = log.best(self.config.metric.direction)
        if best is not None:
            lines.append(
                f"Best {self.config.metric.name} so far: {best.metric_value}"
            )

        # Summary
        summary = log.summary()
        if summary["total"] > 0:
            lines.append(
                f"Experiments: {summary['total']} total, "
                f"{summary['kept']} kept, "
                f"{summary['discarded']} discarded, "
                f"{summary['crashed']} crashed"
            )

        # Editable files
        editable = ", ".join(self.config.files.editable)
        lines.append(f"Editable files: {editable}")

        return "\n".join(lines)

    def _section_history(self, log: ResultsLog) -> str:
        """Section 3: Experiment history table and patterns observed."""
        cw = self.config.agent.context_window
        if not cw.include_history:
            return ""

        records = log.read_last(cw.history_limit)
        if not records:
            return ""

        lines = ["## Experiment History"]
        lines.append("")
        lines.append("| Commit | Metric | Status | Description |")
        lines.append("|--------|--------|--------|-------------|")
        for r in records:
            lines.append(
                f"| {r.commit} | {r.metric_value} | {r.status} | {r.description} |"
            )

        # Patterns observed: group kept/discarded/crashed descriptions, last 5 each
        all_records = log.read_all()
        kept = [r.description for r in all_records if r.status == "keep"][-5:]
        discarded = [r.description for r in all_records if r.status == "discard"][-5:]
        crashed = [r.description for r in all_records if r.status == "crash"][-5:]

        if kept or discarded or crashed:
            lines.append("")
            lines.append("### Patterns Observed")
            if kept:
                lines.append(f"Kept: {', '.join(kept)}")
            if discarded:
                lines.append(f"Discarded: {', '.join(discarded)}")
            if crashed:
                lines.append(f"Crashed: {', '.join(crashed)}")

        return "\n".join(lines)

    def _section_errors(self) -> str:
        """Render queued errors and crash info."""
        parts = []
        if self._errors:
            parts.append("## Errors\n")
            for msg in self._errors:
                parts.append(f"- {msg}")
        if self._crash_info:
            parts.append("## Crash Info\n")
            for info in self._crash_info:
                parts.append(f"```\n{info}\n```")
        return "\n".join(parts) if parts else ""

    def _section_directive(self) -> str:
        """Section 4: Action directive."""
        editable = ", ".join(self.config.files.editable)
        return (
            "## Your Task\n\n"
            "You MUST use the Edit or Write tool to modify the editable files directly. "
            "Do NOT just describe changes — actually edit the code.\n\n"
            "1. Read the current editable files to understand the baseline\n"
            "2. Decide on ONE improvement to try\n"
            "3. Use the Edit/Write tool to apply your changes to: " + editable + "\n"
            "4. After editing, briefly state what you changed and why (one line)\n\n"
            "Do NOT output the full file contents in your response. Just make the edits."
        )

    def assemble(self, log: ResultsLog) -> str:
        """Assemble all sections into a complete prompt."""
        sections = [
            self._section_instructions(),
            self._section_state(log),
            self._section_history(log),
            self._section_errors(),
            self._section_directive(),
        ]
        # Filter empty sections and join
        prompt = "\n\n".join(s for s in sections if s)

        # Clear transient context after assembly
        self._errors.clear()
        self._crash_info.clear()

        return prompt
