"""Tests for preflight checks."""

from unittest.mock import patch, MagicMock
import subprocess as _subprocess

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
    """No exception when claude CLI works and is logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(returncode=0, stdout='{"loggedIn": true}', stderr="")
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise


def test_check_claude_cli_auth_logged_in():
    """No exception when auth status shows logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(returncode=0, stdout='{"loggedIn": true}', stderr="")
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise


def test_check_claude_cli_auth_not_logged_in():
    """Raise when auth status shows not logged in."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(returncode=0, stdout='{"loggedIn": false}', stderr="")
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        with pytest.raises(click.ClickException, match="not logged in"):
            check_claude_cli()


def test_check_claude_cli_auth_json_error():
    """Raise when auth output is not valid JSON and exit code non-zero."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(returncode=1, stdout="not json", stderr="some error")
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        with pytest.raises(click.ClickException, match="Cannot determine"):
            check_claude_cli()


def test_check_claude_cli_auth_old_cli(capsys):
    """Warn but don't raise when CLI doesn't support auth status."""
    version_result = MagicMock(returncode=0)
    auth_result = MagicMock(returncode=1, stdout="", stderr="error: unknown command 'auth'")
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[version_result, auth_result]),
    ):
        check_claude_cli()  # should not raise
    captured = capsys.readouterr()
    assert "too old" in captured.err


def test_check_claude_cli_auth_timeout(capsys):
    """Warn but don't raise when auth status times out."""
    version_result = MagicMock(returncode=0)
    with (
        patch("crucible.preflight.shutil.which", return_value="/usr/bin/claude"),
        patch("crucible.preflight.subprocess.run", side_effect=[
            version_result,
            _subprocess.TimeoutExpired(cmd="claude", timeout=10),
        ]),
    ):
        check_claude_cli()  # should not raise
    captured = capsys.readouterr()
    assert "timed out" in captured.err
