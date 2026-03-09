"""Git manager for branch, commit, tag, and reset operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List


class GitManager:
    """Manages git operations within a workspace directory."""

    def __init__(
        self,
        workspace: Path | str,
        branch_prefix: str = "crucible",
        tag_failed: bool = True,
    ) -> None:
        self.workspace = Path(workspace)
        self.branch_prefix = branch_prefix
        self.tag_failed = tag_failed

    def _run(self, *args: str) -> str:
        """Run a git command in the workspace and return stdout."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def create_branch(self, tag: str) -> None:
        """Create and checkout a new branch with the configured prefix."""
        branch_name = f"{self.branch_prefix}/{tag}"
        self._run("checkout", "-b", branch_name)

    def commit(self, message: str) -> None:
        """Stage all changes and commit with the given message."""
        self._run("add", "-A")
        self._run("commit", "-m", message)

    def head(self) -> str:
        """Return the 7-character short hash of HEAD."""
        return self._run("rev-parse", "--short", "HEAD")

    def tag_failed_and_reset(self, tag: str, seq: int) -> None:
        """Tag the current HEAD as failed, then reset to the parent commit."""
        tag_name = f"failed/{tag}/{seq}"
        self._run("tag", tag_name)
        self._run("reset", "--hard", "HEAD~1")

    def modified_files(self) -> List[str]:
        """Return a list of modified (unstaged) file paths."""
        output = self._run("diff", "--name-only")
        if not output:
            return []
        return output.splitlines()

    def revert_changes(self) -> None:
        """Discard all working tree changes and remove untracked files."""
        self._run("checkout", "--", ".")
        self._run("clean", "-fd")
