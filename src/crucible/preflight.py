"""Preflight checks — fail fast before starting an experiment."""

from __future__ import annotations

import shutil
import subprocess

import click


def check_claude_cli() -> None:
    """Verify that the claude CLI is installed and responsive.

    Raises click.ClickException with actionable guidance on failure.
    """
    if not shutil.which("claude"):
        raise click.ClickException(
            "claude CLI not found on PATH.\n"
            "Install: npm install -g @anthropic-ai/claude-code\n"
            "Then authenticate: claude login"
        )

    result = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise click.ClickException(
            "claude CLI found but not working.\n"
            f"Error: {result.stderr.strip()}\n"
            "Try: claude login"
        )
