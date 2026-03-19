"""Tests for preflight checks."""

from unittest.mock import patch

import click
import pytest

from crucible.preflight import check_claude_cli


def test_check_claude_cli_not_found():
    """Raise if claude CLI is not on PATH."""
    with patch("crucible.preflight.shutil.which", return_value=None):
        with pytest.raises(click.ClickException, match="claude CLI not found"):
            check_claude_cli()


def test_check_claude_cli_broken(tmp_path):
    """Raise if claude CLI exits non-zero."""
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "some error"
        with pytest.raises(click.ClickException, match="not working"):
            check_claude_cli()


def test_check_claude_cli_ok():
    """No exception when claude CLI works."""
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        check_claude_cli()  # should not raise
