"""Scratch-dir isolation for CLI subscription adapters — M3 PR 16.

Reviewer round 1 Q8 reframe: the scratch-dir approach is NOT security
isolation — the CLI runs unsandboxed on the host fs. It IS:
  - **Reproducibility**: the agent only sees declared editable +
    readonly files; no accidental cross-talk between attempts.
  - **Clean diff capture**: only files we copied IN can be considered
    "modified" — anything outside the scratch is host-state we don't
    track.
  - **Truth-in-labeling**: every CLI-subscription run is tagged
    `isolation="cli_subscription_unsandboxed"` in the ledger
    (parallel to spec §11.2 Q5's `isolation="local_unsafe"`), so
    callers / reports can branch on the degraded ACL.

Per spec §INV-1, marketing wording must use "no bypass observed in N
adversarial trials" rather than "secure" — the scratch dir doesn't
prevent the CLI from reading host files / hitting the network.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from crucible.security.cheat_resistance_policy import CheatResistancePolicy


@contextmanager
def cli_scratch_dir(
    *,
    workspace: Path,
    policy: CheatResistancePolicy,
) -> Iterator[Path]:
    """Yield a temp directory containing copies of editable + readonly
    files only (no hidden / unlisted). The CLI is invoked with cwd set
    to this scratch dir, so a well-behaved CLI sees only what we want
    it to see. Modified files are detected by mtime diff after the run.

    Reviewer round 1 Q8: this is reproducibility, not security. A
    misbehaving CLI can `cat /etc/passwd` from anywhere. Use Docker
    mode (spec §INV-2) for actual isolation.

    Caller is responsible for copying any modified files BACK from the
    scratch dir to the real workspace. This helper just sets up the
    inbound copy.

    Yields: absolute Path to the scratch dir. Cleaned up on exit.
    """
    workspace_abs = workspace.resolve()
    scratch = Path(tempfile.mkdtemp(prefix="crucible-cli-scratch-"))
    try:
        # Copy each editable / readonly file. We deliberately do NOT
        # `cp -r` the whole workspace; only declared files are visible.
        for source in list(policy.editable) + list(policy.readonly):
            try:
                rel = source.relative_to(workspace_abs)
            except ValueError:
                # Files outside the workspace can't be safely placed
                # in the scratch — skip with a log opportunity.
                continue
            dest = scratch / rel
            if not source.exists():
                # Editable file may not exist yet (agent will create)
                # — ensure parent dir but don't error.
                dest.parent.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, dest)
            elif source.is_dir():
                shutil.copytree(source, dest, dirs_exist_ok=True)
        yield scratch
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def copy_editable_changes_back(
    *,
    scratch: Path,
    workspace: Path,
    policy: CheatResistancePolicy,
) -> list[Path]:
    """Copy mutated editable files from scratch back to the workspace.

    Returns the list of workspace paths that received an update.
    Readonly / hidden / unlisted paths are NEVER copied back, even if
    the scratch contains them — this prevents a CLI run from mutating
    files the policy says shouldn't be agent-writable.
    """
    workspace_abs = workspace.resolve()
    modified: list[Path] = []
    for editable in policy.editable:
        try:
            rel = editable.relative_to(workspace_abs)
        except ValueError:
            continue
        scratch_copy = scratch / rel
        if not scratch_copy.exists() or not scratch_copy.is_file():
            continue
        # Only mark modified if content actually changed.
        try:
            new_bytes = scratch_copy.read_bytes()
        except OSError:
            continue
        if editable.exists():
            try:
                if editable.read_bytes() == new_bytes:
                    continue
            except OSError:
                pass
        editable.parent.mkdir(parents=True, exist_ok=True)
        editable.write_bytes(new_bytes)
        modified.append(editable)
    return modified
