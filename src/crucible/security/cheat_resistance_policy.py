"""Single source of truth for file-level access control.

POC Day 1 scope: classify any path as editable / readonly / hidden / unlisted,
with hardening against path traversal, symlinks, and hardlinks.

Future: `guardrails.py`, agent backend hooks, and Docker shadow-mount logic
will all read from a single `CheatResistancePolicy` instance instead of
each maintaining their own version.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Classification = Literal["editable", "readonly", "hidden", "unlisted"]


class PolicyViolation(Exception):
    """Raised when an operation violates the access policy."""

    def __init__(self, classification: Classification, path: Path, reason: str) -> None:
        super().__init__(f"{classification}: {path} — {reason}")
        self.classification = classification
        self.path = path
        self.reason = reason


@dataclass
class CheatResistancePolicy:
    """SSOT for path classification.

    All paths are resolved to absolute form during construction. Classification
    follows this priority order:

      1. Workspace boundary (paths outside workspace → unlisted)
      2. Inode collision (hardlink to a hidden/readonly file → that classification)
      3. Direct path match (after symlink resolution)
      4. Default → unlisted
    """

    workspace: Path
    editable: set[Path] = field(default_factory=set)
    readonly: set[Path] = field(default_factory=set)
    hidden: set[Path] = field(default_factory=set)

    @classmethod
    def from_lists(
        cls,
        workspace: Path | str,
        editable: list[str] | None = None,
        readonly: list[str] | None = None,
        hidden: list[str] | None = None,
    ) -> CheatResistancePolicy:
        ws = Path(workspace).resolve()

        def _norm(rel_paths: list[str] | None) -> set[Path]:
            if not rel_paths:
                return set()
            out: set[Path] = set()
            for p in rel_paths:
                pp = Path(p)
                if pp.is_absolute():
                    out.add(pp.resolve())
                else:
                    out.add((ws / pp).resolve())
            return out

        return cls(
            workspace=ws,
            editable=_norm(editable),
            readonly=_norm(readonly),
            hidden=_norm(hidden),
        )

    def _resolve_safely(self, path: Path | str) -> Path | None:
        """Resolve a path, returning None if it cannot be made absolute.

        We DO follow symlinks here — that's the entire point. After resolve(),
        the returned path is the actual file system target; classification then
        operates on that target. A symlink in the workspace pointing OUT is
        therefore resolved and caught by the workspace-boundary check.
        """
        try:
            p = Path(path)
            if not p.is_absolute():
                p = self.workspace / p
            return p.resolve(strict=False)
        except (OSError, ValueError):
            return None

    def _is_within_workspace(self, abs_path: Path) -> bool:
        try:
            abs_path.relative_to(self.workspace)
            return True
        except ValueError:
            return False

    def _inode_collides(self, abs_path: Path, candidates: set[Path]) -> bool:
        """Return True if abs_path shares an inode with any path in candidates.

        Catches hardlink attacks. Both files must exist.
        """
        if not abs_path.exists():
            return False
        try:
            target_ino = abs_path.stat().st_ino
            target_dev = abs_path.stat().st_dev
        except OSError:
            return False
        for cand in candidates:
            if not cand.exists():
                continue
            try:
                cand_stat = cand.stat()
            except OSError:
                continue
            if cand_stat.st_ino == target_ino and cand_stat.st_dev == target_dev:
                # Allow exact same path — that's not a hardlink, it's the same entry.
                if cand.samefile(abs_path) and cand == abs_path:
                    continue
                return True
        return False

    def classify(self, path: Path | str) -> Classification:
        """Return the classification of `path`.

        Priority:
          1. Outside workspace → unlisted
          2. Hardlink to hidden → hidden
          3. Hardlink to readonly → readonly
          4. Resolved path in hidden set → hidden
          5. Resolved path in readonly set → readonly
          6. Resolved path in editable set → editable
          7. Otherwise → unlisted
        """
        abs_path = self._resolve_safely(path)
        if abs_path is None:
            return "unlisted"

        if not self._is_within_workspace(abs_path):
            return "unlisted"

        # Hardlink collision wins over direct match — defends against
        # `ln evaluate.py solution.py` style attacks.
        if self._inode_collides(abs_path, self.hidden):
            return "hidden"
        if self._inode_collides(abs_path, self.readonly):
            return "readonly"

        if abs_path in self.hidden:
            return "hidden"
        if abs_path in self.readonly:
            return "readonly"
        if abs_path in self.editable:
            return "editable"

        return "unlisted"

    def is_writable_by_agent(self, path: Path | str) -> bool:
        """True iff path is in the editable whitelist."""
        return self.classify(path) == "editable"

    def is_visible_to_agent(self, path: Path | str) -> bool:
        """True iff path is editable or readonly. Hidden and unlisted are invisible."""
        cls = self.classify(path)
        return cls in ("editable", "readonly")

    def shadow_paths(self) -> list[Path]:
        """Hidden paths that must be `--mount=type=tmpfs` or `/dev/null` overlaid in containers."""
        return sorted(self.hidden)

    def assert_writable(self, path: Path | str) -> None:
        """Raise PolicyViolation if the agent attempts to write to a non-editable path."""
        cls = self.classify(path)
        if cls != "editable":
            abs_path = self._resolve_safely(path) or Path(path)
            raise PolicyViolation(
                classification=cls,
                path=abs_path,
                reason=f"agent cannot write to {cls} files",
            )

    def assert_visible(self, path: Path | str) -> None:
        """Raise PolicyViolation if the agent attempts to read a hidden/unlisted path."""
        if not self.is_visible_to_agent(path):
            abs_path = self._resolve_safely(path) or Path(path)
            cls = self.classify(path)
            raise PolicyViolation(
                classification=cls,
                path=abs_path,
                reason=f"agent cannot see {cls} files",
            )
