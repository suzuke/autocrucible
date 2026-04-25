"""POC Day 1 — hardcoded ACL boundary tests.

Goal: prove that `CheatResistancePolicy` correctly classifies and rejects
common attack vectors. ~30 cases across 5 attack families:

  1. Path traversal
  2. Absolute path escape
  3. Symlink redirection
  4. Hardlink collision
  5. Glob / case / unicode mutation

Each test sets up a workspace with three files:
  - `solution.py`   editable
  - `evaluate.py`   readonly
  - `.env`          hidden

Then attempts to read or write through various attack patterns. Every attack
must be rejected (returns the expected classification or raises PolicyViolation).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from crucible.security import CheatResistancePolicy, PolicyViolation


# ---------------------------------------------------------------------------
# Fixture: workspace with editable / readonly / hidden files
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "solution.py").write_text("# editable")
    (tmp_path / "evaluate.py").write_text("# readonly")
    (tmp_path / ".env").write_text("SECRET=42")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "data.txt").write_text("data")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> CheatResistancePolicy:
    return CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["solution.py"],
        readonly=["evaluate.py"],
        hidden=[".env"],
    )


# ---------------------------------------------------------------------------
# Family 1 — direct classification (sanity)
# ---------------------------------------------------------------------------


def test_editable_classified_correctly(policy, workspace):
    assert policy.classify(workspace / "solution.py") == "editable"


def test_readonly_classified_correctly(policy, workspace):
    assert policy.classify(workspace / "evaluate.py") == "readonly"


def test_hidden_classified_correctly(policy, workspace):
    assert policy.classify(workspace / ".env") == "hidden"


def test_unlisted_file_is_unlisted(policy, workspace):
    (workspace / "random.py").write_text("")
    assert policy.classify(workspace / "random.py") == "unlisted"


def test_subdir_unlisted_file(policy, workspace):
    assert policy.classify(workspace / "subdir" / "data.txt") == "unlisted"


# ---------------------------------------------------------------------------
# Family 2 — relative path traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "../etc/passwd",
        "../../etc/passwd",
        "../../../etc/passwd",
        "subdir/../../etc/passwd",
        "solution.py/../../../etc/passwd",
        "./solution.py/../evaluate.py/../../etc/passwd",
    ],
)
def test_path_traversal_blocked(policy, workspace, attack):
    """Relative paths that escape workspace must be classified `unlisted` (denied)."""
    cls = policy.classify(workspace / attack)
    assert cls == "unlisted", f"traversal {attack!r} leaked → {cls}"


# ---------------------------------------------------------------------------
# Family 3 — absolute path escape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "abs_attack",
    [
        "/etc/passwd",
        "/etc/shadow",
        "/usr/bin/env",
        "/tmp/whatever",
        "/var/log/system.log",
    ],
)
def test_absolute_path_outside_workspace_blocked(policy, abs_attack):
    assert policy.classify(abs_attack) == "unlisted"


def test_home_dir_blocked(policy):
    assert policy.classify(str(Path.home())) == "unlisted"


# ---------------------------------------------------------------------------
# Family 4 — symlink attacks
# ---------------------------------------------------------------------------


def test_symlink_to_readonly_classified_as_readonly(policy, workspace):
    """Symlink in editable position pointing to readonly file must NOT be writable.

    Setup: solution.py is editable. We replace it with a symlink to evaluate.py
    (readonly). Classification should follow the symlink and reject as readonly.
    """
    sol = workspace / "solution.py"
    sol.unlink()
    sol.symlink_to(workspace / "evaluate.py")
    assert policy.classify(sol) == "readonly"
    assert not policy.is_writable_by_agent(sol)


def test_symlink_to_hidden_classified_as_hidden(policy, workspace):
    sol = workspace / "solution.py"
    sol.unlink()
    sol.symlink_to(workspace / ".env")
    assert policy.classify(sol) == "hidden"
    assert not policy.is_visible_to_agent(sol)


def test_symlink_pointing_outside_workspace_blocked(policy, workspace):
    sol = workspace / "solution.py"
    sol.unlink()
    sol.symlink_to("/etc/passwd")
    # /etc/passwd is outside workspace → unlisted
    assert policy.classify(sol) == "unlisted"


def test_new_symlink_in_editable_dir_pointing_to_hidden(policy, workspace):
    """Agent creates a new symlink `clone.py` -> `.env`. Must be classified hidden."""
    clone = workspace / "clone.py"
    clone.symlink_to(workspace / ".env")
    assert policy.classify(clone) == "hidden"


# ---------------------------------------------------------------------------
# Family 5 — hardlink attacks
# ---------------------------------------------------------------------------


def test_hardlink_to_readonly_caught_via_inode(policy, workspace):
    """Agent creates `solution.py` as a hardlink to `evaluate.py`.

    Both files share an inode; classification must detect this and treat
    the hardlink as readonly (since modifying solution.py would alter evaluate.py).
    """
    sol = workspace / "solution.py"
    sol.unlink()
    os.link(workspace / "evaluate.py", sol)
    assert policy.classify(sol) == "readonly"
    assert not policy.is_writable_by_agent(sol)


def test_hardlink_to_hidden_caught_via_inode(policy, workspace):
    sol = workspace / "solution.py"
    sol.unlink()
    os.link(workspace / ".env", sol)
    assert policy.classify(sol) == "hidden"
    assert not policy.is_visible_to_agent(sol)


def test_new_hardlink_in_unlisted_position_to_readonly(policy, workspace):
    """Agent creates `clone.py` as hardlink to evaluate.py. Must be classified readonly."""
    clone = workspace / "clone.py"
    os.link(workspace / "evaluate.py", clone)
    assert policy.classify(clone) == "readonly"


# ---------------------------------------------------------------------------
# Family 6 — case / encoding mutations
# ---------------------------------------------------------------------------


def test_uppercase_path_does_not_match_lowercase_editable(policy, workspace):
    """On case-sensitive FS (Linux), Solution.py is a different file from solution.py.

    On macOS APFS default (case-insensitive), the FS resolves both to the same inode.
    Either way: classification must be consistent — we test that this doesn't
    accidentally upgrade an unlisted file.
    """
    upper = workspace / "Solution.py"
    cls = policy.classify(upper)
    # Either it doesn't exist (case-sensitive FS), OR it exists and points to
    # the same inode as solution.py (case-insensitive FS, in which case it's
    # the SAME file and editable). Both outcomes are correct.
    assert cls in ("unlisted", "editable")


def test_redundant_separators_normalized(policy, workspace):
    """Path with extra slashes / dots normalizes correctly."""
    weird = workspace / "." / "solution.py"
    assert policy.classify(weird) == "editable"
    weirder = workspace / "subdir" / ".." / "solution.py"
    assert policy.classify(weirder) == "editable"


def test_empty_string_unlisted(policy):
    assert policy.classify("") == "unlisted"


# ---------------------------------------------------------------------------
# Family 7 — assert_* helpers raise on violation
# ---------------------------------------------------------------------------


def test_assert_writable_passes_for_editable(policy, workspace):
    policy.assert_writable(workspace / "solution.py")  # no raise


def test_assert_writable_raises_for_readonly(policy, workspace):
    with pytest.raises(PolicyViolation) as exc:
        policy.assert_writable(workspace / "evaluate.py")
    assert exc.value.classification == "readonly"


def test_assert_writable_raises_for_hidden(policy, workspace):
    with pytest.raises(PolicyViolation) as exc:
        policy.assert_writable(workspace / ".env")
    assert exc.value.classification == "hidden"


def test_assert_writable_raises_for_unlisted(policy, workspace):
    (workspace / "random.py").write_text("")
    with pytest.raises(PolicyViolation):
        policy.assert_writable(workspace / "random.py")


def test_assert_visible_passes_for_editable_and_readonly(policy, workspace):
    policy.assert_visible(workspace / "solution.py")
    policy.assert_visible(workspace / "evaluate.py")


def test_assert_visible_raises_for_hidden(policy, workspace):
    with pytest.raises(PolicyViolation) as exc:
        policy.assert_visible(workspace / ".env")
    assert exc.value.classification == "hidden"


def test_assert_visible_raises_for_unlisted(policy, workspace):
    (workspace / "random.py").write_text("")
    with pytest.raises(PolicyViolation):
        policy.assert_visible(workspace / "random.py")


# ---------------------------------------------------------------------------
# Family 8 — non-existent / partially-resolved paths
# ---------------------------------------------------------------------------


def test_nonexistent_path_in_editable_position(policy, workspace):
    """`solution.py` removed; agent tries to write a NEW file at that path.

    This is the legitimate flow for a fresh experiment where solution.py
    doesn't exist yet. Should still be classified editable.
    """
    (workspace / "solution.py").unlink()
    assert policy.classify(workspace / "solution.py") == "editable"


def test_nonexistent_path_outside_editable(policy, workspace):
    assert policy.classify(workspace / "newfile.py") == "unlisted"
