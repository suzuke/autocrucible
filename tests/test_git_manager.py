"""Tests for GitManager beam and reset methods."""
import subprocess
from pathlib import Path

import pytest

from crucible.git_manager import GitManager


def setup_git_repo(tmp_path: Path) -> GitManager:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return GitManager(workspace=tmp_path)


def test_reset_to_commit(tmp_path):
    gm = setup_git_repo(tmp_path)
    baseline = gm.head()
    (tmp_path / "file.py").write_text("x = 2\n")
    gm.commit("second")
    assert gm.head() != baseline
    gm.reset_to_commit(baseline)
    assert gm.head() == baseline


def test_create_beam_branches(tmp_path):
    gm = setup_git_repo(tmp_path)
    gm.create_branch("run1")
    baseline = gm.head()
    gm.create_beam_branches("run1", beam_width=3)
    for i in range(3):
        branch = f"crucible/run1-beam-{i}"
        result = subprocess.run(
            ["git", "rev-parse", "--short", branch],
            cwd=tmp_path, capture_output=True, text=True
        )
        assert result.stdout.strip() == baseline


def test_checkout_beam(tmp_path):
    gm = setup_git_repo(tmp_path)
    gm.create_branch("run1")
    gm.create_beam_branches("run1", beam_width=2)
    gm.checkout_beam("run1", 0)
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path, capture_output=True, text=True
    )
    assert result.stdout.strip() == "crucible/run1-beam-0"
