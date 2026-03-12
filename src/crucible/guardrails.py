"""Guard rails for edit validation and metric checks."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Violation:
    """Represents a guard-rail violation."""

    kind: str  # "readonly" | "unlisted" | "no_edits"
    message: str


class GuardRails:
    """Validates file edits against editable/readonly policies and checks metrics."""

    def __init__(self, editable: list[str], readonly: list[str]) -> None:
        self.editable: set[str] = set(editable)
        self.readonly: set[str] = set(readonly)

    def check_edits(self, modified_files: list[str]) -> Violation | None:
        """Check whether the list of modified files violates any policy.

        Returns None if all edits are valid, or a Violation describing the problem.
        """
        if not modified_files:
            return Violation(kind="no_edits", message="No files were edited.")

        for f in modified_files:
            if f.startswith(".crucible/"):
                return Violation(
                    kind="readonly",
                    message=f"File is read-only (platform-protected): {f}",
                )
            if f in self.readonly:
                return Violation(
                    kind="readonly",
                    message=f"File is read-only: {f}",
                )
            if f not in self.editable:
                return Violation(
                    kind="unlisted",
                    message=f"File is not in the editable list: {f}",
                )

        return None

    def check_metric(self, value: float) -> bool:
        """Return False if the metric value is NaN or infinite."""
        if math.isnan(value) or math.isinf(value):
            return False
        return True
