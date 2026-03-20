"""Preflight checks — fail fast before starting an experiment."""

from __future__ import annotations

import json
import shutil
import subprocess

import click


def check_claude_cli() -> None:
    """Verify that the claude CLI is installed, responsive, and logged in.

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

    # Check login status
    try:
        auth = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        click.echo(
            "Warning: claude auth status timed out, skipping auth check",
            err=True,
        )
        return

    try:
        data = json.loads(auth.stdout)
        if not data.get("loggedIn"):
            raise click.ClickException(
                "claude CLI is not logged in.\n"
                "Run: claude login"
            )
    except json.JSONDecodeError:
        if auth.returncode != 0:
            stderr = auth.stderr.strip() or auth.stdout.strip()
            if "unknown command" in stderr.lower():
                click.echo(
                    "Warning: claude CLI too old to check auth status; "
                    "consider updating. Proceeding anyway.",
                    err=True,
                )
            else:
                raise click.ClickException(
                    "Cannot determine claude auth status.\n"
                    f"Output: {stderr}\n"
                    "Try: claude login"
                )
