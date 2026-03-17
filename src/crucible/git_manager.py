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

    def create_branch_from(self, tag: str, commit: str) -> None:
        """Create and checkout a new branch starting from a specific commit."""
        branch_name = f"{self.branch_prefix}/{tag}"
        self._run("checkout", "-b", branch_name, commit)

    def commit(self, message: str) -> None:
        """Stage all changes and commit with the given message."""
        self._run("add", "-A")
        self._run("commit", "-m", message)

    def head(self) -> str:
        """Return the 7-character short hash of HEAD."""
        return self._run("rev-parse", "--short", "HEAD")

    def tag_failed_and_reset(self, tag: str, seq: int) -> None:
        """Tag the current HEAD as failed, then reset to the parent commit."""
        if self.tag_failed:
            tag_name = f"failed/{tag}/{seq}"
            # Use -f to overwrite if tag already exists (can happen on resume)
            self._run("tag", "-f", tag_name)
        self._run("reset", "--hard", "HEAD~1")

    def modified_files(self) -> List[str]:
        """Return a list of modified (unstaged) file paths."""
        output = self._run("diff", "--name-only")
        if not output:
            return []
        return output.splitlines()

    def branch_exists(self, tag: str) -> bool:
        """Check if the experiment branch already exists."""
        branch_name = f"{self.branch_prefix}/{tag}"
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=self.workspace, capture_output=True, text=True,
        )
        return bool(result.stdout.strip())

    def checkout_branch(self, tag: str) -> None:
        """Checkout an existing experiment branch."""
        branch_name = f"{self.branch_prefix}/{tag}"
        self._run("checkout", branch_name)

    def show_file(self, tag: str, file_path: str) -> str:
        """Read a file's content from a specific experiment branch."""
        branch_name = f"{self.branch_prefix}/{tag}"
        return self._run("show", f"{branch_name}:{file_path}")

    def reset_to_commit(self, commit: str) -> None:
        """Hard-reset HEAD to a specific commit (e.g., baseline)."""
        self._run("reset", "--hard", commit)

    def create_beam_branches(self, tag: str, beam_width: int) -> None:
        """Create beam_width branches all starting at the current HEAD."""
        current = self.head()
        for i in range(beam_width):
            beam_branch = f"{self.branch_prefix}/{tag}-beam-{i}"
            self._run("branch", beam_branch, current)

    def checkout_beam(self, tag: str, beam_id: int) -> None:
        """Checkout the beam branch for the given beam_id."""
        branch_name = f"{self.branch_prefix}/{tag}-beam-{beam_id}"
        self._run("checkout", branch_name)

    def revert_changes(self) -> None:
        """Discard all working tree changes and remove untracked files."""
        self._run("checkout", "--", ".")
        self._run("clean", "-fd")
