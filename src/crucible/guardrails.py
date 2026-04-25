"""Guard rails for edit validation and metric checks.

M1b: GuardRails now delegates path classification to
`crucible.security.CheatResistancePolicy` (the SSOT). The legacy
string-only API is preserved for backward compatibility — callers that
do NOT provide a workspace at construction time get the original
behaviour. Callers that DO provide a workspace get full SSOT semantics
including symlink/hardlink/path-traversal defenses.

External callers see the same `GuardRails(editable, readonly).check_edits([...])`
API as before; nothing breaks. Orchestrator now constructs with the
workspace path so all benefit from the unified policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from crucible.security import CheatResistancePolicy


@dataclass
class Violation:
    """Represents a guard-rail violation."""

    kind: str  # "readonly" | "unlisted" | "hidden" | "no_edits"
    message: str


class GuardRails:
    """Validates file edits against editable/readonly/hidden policies.

    Construction:
      GuardRails(editable=[...], readonly=[...])              # legacy, string-only
      GuardRails(editable, readonly, hidden, workspace=path)  # SSOT mode

    In SSOT mode, classification goes through CheatResistancePolicy and
    transparently catches symlink redirection, hardlink aliasing,
    workspace boundary escapes, and path-traversal patterns.

    In legacy mode, only literal string membership is checked (preserves
    pre-M1b behaviour for callers that haven't migrated).
    """

    def __init__(
        self,
        editable: list[str],
        readonly: list[str],
        hidden: list[str] | None = None,
        workspace: Path | str | None = None,
    ) -> None:
        self.editable: set[str] = set(editable)
        self.readonly: set[str] = set(readonly)
        self.hidden: set[str] = set(hidden or [])
        self._policy: Optional[CheatResistancePolicy] = None
        if workspace is not None:
            self._policy = CheatResistancePolicy.from_lists(
                workspace=workspace,
                editable=editable,
                readonly=readonly,
                hidden=hidden or [],
            )

    # ---- SSOT migration helper -------------------------------------------

    @property
    def policy(self) -> Optional[CheatResistancePolicy]:
        """Expose the underlying policy when constructed with a workspace.

        Returns None in legacy (workspace-less) mode. Callers that need
        symlink/hardlink defenses should ensure they constructed with
        workspace= and use this property.
        """
        return self._policy

    def add_editable(self, rel_path: str) -> None:
        """Append a path to the editable whitelist.

        Updates both the legacy `self.editable` set AND the underlying
        CheatResistancePolicy (in SSOT mode), so they stay in sync.
        Used by orchestrator's `allow_install` path to grant the agent
        permission to edit requirements.txt mid-run.
        """
        self.editable.add(rel_path)
        if self._policy is not None:
            # CheatResistancePolicy stores absolute paths; rebuild the
            # editable set to include the new entry.
            new_path = (self._policy.workspace / rel_path).resolve()
            self._policy.editable.add(new_path)

    # ---- edit validation -------------------------------------------------

    def check_edits(self, modified_files: list[str]) -> Violation | None:
        """Check whether the list of modified files violates any policy.

        Returns None if all edits are valid, or a Violation describing the
        problem.
        """
        if not modified_files:
            return Violation(kind="no_edits", message="No files were edited.")

        if self._policy is not None:
            return self._check_via_policy(modified_files)
        return self._check_legacy(modified_files)

    def _check_via_policy(self, modified_files: list[str]) -> Violation | None:
        """SSOT-mode check: each path runs through CheatResistancePolicy.classify."""
        assert self._policy is not None
        for f in modified_files:
            # `.crucible/` paths are always platform-protected even if the
            # user didn't list them (defense in depth).
            if f.startswith(".crucible/"):
                return Violation(
                    kind="readonly",
                    message=f"File is read-only (platform-protected): {f}",
                )
            cls = self._policy.classify(f)
            if cls == "hidden":
                return Violation(
                    kind="hidden",
                    message=f"File is hidden (cannot be modified): {f}",
                )
            if cls == "readonly":
                return Violation(
                    kind="readonly",
                    message=f"File is read-only: {f}",
                )
            if cls != "editable":
                return Violation(
                    kind="unlisted",
                    message=f"File is not in the editable list: {f}",
                )
        return None

    def _check_legacy(self, modified_files: list[str]) -> Violation | None:
        """Legacy string-only check (no symlink/hardlink/traversal defenses).

        Behaviour identical to pre-M1b GuardRails. Triggered when caller
        did NOT provide a workspace at construction time.
        """
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
            if f in self.hidden:
                return Violation(
                    kind="hidden",
                    message=f"File is hidden (cannot be modified): {f}",
                )
            if f not in self.editable:
                return Violation(
                    kind="unlisted",
                    message=f"File is not in the editable list: {f}",
                )
        return None

    # ---- metric validation -----------------------------------------------

    def check_metric(self, value: float) -> bool:
        """Return False if the metric value is NaN or infinite."""
        if math.isnan(value) or math.isinf(value):
            return False
        return True
