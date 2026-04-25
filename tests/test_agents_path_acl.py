"""Tests for `crucible.agents._path_acl` — M2 PR 13.

These tests verify the Crucible-owned filesystem-access layer that all
backends (smolagents, future SubscriptionCLIBackend) call from their
tools. They're independent of smolagents — verifying ACL, path
normalization, error message hygiene, and forbidden-path rejection
without depending on any agent framework.

Reviewer round 1 pin: ACL / path-norm / forbidden-tool tests must NOT
all be importorskip on smolagents.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from crucible.agents._path_acl import (
    DEFAULT_GREP_MATCH_LIMIT,
    DEFAULT_READ_LIMIT_BYTES,
    ToolDenied,
    safe_edit,
    safe_glob,
    safe_grep,
    safe_read,
    safe_write,
)
from crucible.security.cheat_resistance_policy import CheatResistancePolicy


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace with a small file layout exercising all classifications.

    Layout:
        ws/
        ├── train.py         (editable)
        ├── helper.py        (editable)
        ├── README.md        (readonly)
        ├── evaluate.py      (hidden)
        └── secret.txt       (unlisted)
    """
    (tmp_path / "train.py").write_text("def train():\n    return 0.5\n")
    (tmp_path / "helper.py").write_text("def helper():\n    pass\n")
    (tmp_path / "README.md").write_text("# Project\n")
    (tmp_path / "evaluate.py").write_text("HIDDEN_BENCHMARK = 'secret'\n")
    (tmp_path / "secret.txt").write_text("secret data\n")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "deep.py").write_text("# deep\n")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> CheatResistancePolicy:
    return CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py", "helper.py"],
        readonly=["README.md"],
        hidden=["evaluate.py"],
    )


# ---------------------------------------------------------------------------
# Path normalization (defence-in-depth before policy lookup)
# ---------------------------------------------------------------------------


def test_resolve_rejects_traversal(workspace, policy):
    """Path traversal via `..` must NOT escape the workspace."""
    with pytest.raises(ToolDenied, match="escapes workspace"):
        safe_read("../etc/passwd", policy=policy, workspace=workspace)


def test_resolve_rejects_absolute_outside_workspace(workspace, policy):
    """Absolute path outside the workspace is rejected."""
    with pytest.raises(ToolDenied, match="escapes workspace"):
        safe_read("/etc/passwd", policy=policy, workspace=workspace)


def test_resolve_accepts_absolute_inside_workspace(workspace, policy):
    """Absolute path INSIDE workspace is accepted (after normalization)."""
    abs_path = str(workspace / "train.py")
    out = safe_read(abs_path, policy=policy, workspace=workspace)
    assert "def train" in out


def test_resolve_rejects_empty_path(workspace, policy):
    with pytest.raises(ToolDenied, match="non-empty string"):
        safe_read("", policy=policy, workspace=workspace)


def test_resolve_rejects_non_string_path(workspace, policy):
    with pytest.raises(ToolDenied, match="non-empty string"):
        safe_read(None, policy=policy, workspace=workspace)


# ---------------------------------------------------------------------------
# safe_read — visibility ACL
# ---------------------------------------------------------------------------


def test_safe_read_editable_ok(workspace, policy):
    out = safe_read("train.py", policy=policy, workspace=workspace)
    assert "def train" in out


def test_safe_read_readonly_ok(workspace, policy):
    out = safe_read("README.md", policy=policy, workspace=workspace)
    assert "# Project" in out


def test_safe_read_hidden_denied(workspace, policy):
    """Hidden eval files MUST NOT be readable. Reviewer Q6 pin."""
    with pytest.raises(ToolDenied, match="not visible to the agent"):
        safe_read("evaluate.py", policy=policy, workspace=workspace)


def test_safe_read_unlisted_denied(workspace, policy):
    """Unlisted files (not in any allowlist) are denied with a helpful tip."""
    with pytest.raises(ToolDenied, match="outside the agent's working set"):
        safe_read("secret.txt", policy=policy, workspace=workspace)


def test_safe_read_error_message_does_not_leak_existence(workspace, policy):
    """Hidden file's error message should be SAME whether the file exists
    or not — agent must not be able to probe existence via tool errors."""
    # Hidden file that exists:
    try:
        safe_read("evaluate.py", policy=policy, workspace=workspace)
    except ToolDenied as exc:
        msg_exists = str(exc)

    # Hidden file that doesn't exist (we mark a non-existent path hidden):
    nonexistent_policy = CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py"],
        hidden=["does_not_exist.py"],
    )
    try:
        safe_read("does_not_exist.py", policy=nonexistent_policy, workspace=workspace)
    except ToolDenied as exc:
        msg_missing = str(exc)

    # Both should say "not visible to the agent" — neither hints at existence.
    assert "not visible to the agent" in msg_exists
    assert "not visible to the agent" in msg_missing


def test_safe_read_truncates_at_limit(workspace, policy):
    big = "x" * (DEFAULT_READ_LIMIT_BYTES + 5000)
    (workspace / "train.py").write_text(big)
    out = safe_read("train.py", policy=policy, workspace=workspace)
    assert "[... truncated" in out
    # Truncated portion is at most limit bytes:
    pre_note = out.split("\n[... truncated")[0]
    assert len(pre_note.encode("utf-8")) <= DEFAULT_READ_LIMIT_BYTES


def test_safe_read_missing_file_clear_error(workspace, policy):
    # Add a non-existent editable path so it passes ACL but not exists check
    extended_policy = CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py", "ghost.py"],
    )
    with pytest.raises(ToolDenied, match="file not found"):
        safe_read("ghost.py", policy=extended_policy, workspace=workspace)


# ---------------------------------------------------------------------------
# safe_write — write ACL
# ---------------------------------------------------------------------------


def test_safe_write_editable_ok(workspace, policy):
    safe_write("train.py", "new content", policy=policy, workspace=workspace)
    assert (workspace / "train.py").read_text() == "new content"


def test_safe_write_creates_new_file_in_editable_dir(workspace, policy):
    extended = CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py", "newfile.py"],
    )
    safe_write("newfile.py", "fresh", policy=extended, workspace=workspace)
    assert (workspace / "newfile.py").read_text() == "fresh"


def test_safe_write_readonly_denied(workspace, policy):
    with pytest.raises(ToolDenied, match="read-only"):
        safe_write("README.md", "tampered", policy=policy, workspace=workspace)
    assert (workspace / "README.md").read_text() == "# Project\n"


def test_safe_write_hidden_denied(workspace, policy):
    """Critical: agent must NOT be able to overwrite the hidden eval file."""
    with pytest.raises(ToolDenied, match="not visible to the agent"):
        safe_write("evaluate.py", "PWNED = 1\n", policy=policy, workspace=workspace)
    # Verify the file is unchanged
    assert "HIDDEN_BENCHMARK" in (workspace / "evaluate.py").read_text()


def test_safe_write_unlisted_denied(workspace, policy):
    with pytest.raises(ToolDenied, match="outside the agent's working set"):
        safe_write("new.txt", "x", policy=policy, workspace=workspace)


def test_safe_write_rejects_non_string_content(workspace, policy):
    with pytest.raises(ToolDenied, match="content argument must be a string"):
        safe_write("train.py", b"bytes-not-str", policy=policy, workspace=workspace)


# ---------------------------------------------------------------------------
# safe_edit — search/replace contract (reviewer Q4)
# ---------------------------------------------------------------------------


def test_safe_edit_replaces_unique_substring(workspace, policy):
    safe_edit("train.py", "return 0.5", "return 0.7",
              policy=policy, workspace=workspace)
    assert "return 0.7" in (workspace / "train.py").read_text()


def test_safe_edit_rejects_missing_substring(workspace, policy):
    with pytest.raises(ToolDenied, match="not found"):
        safe_edit("train.py", "missing snippet", "x",
                  policy=policy, workspace=workspace)


def test_safe_edit_rejects_ambiguous_substring(workspace, policy):
    """Ambiguous edits (multiple occurrences) must be rejected — caller
    should narrow the snippet or use write_file for full overwrite."""
    (workspace / "train.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolDenied, match="ambiguous"):
        safe_edit("train.py", "x = 1", "x = 2",
                  policy=policy, workspace=workspace)


def test_safe_edit_rejects_empty_old(workspace, policy):
    with pytest.raises(ToolDenied, match="must not be empty"):
        safe_edit("train.py", "", "new", policy=policy, workspace=workspace)


def test_safe_edit_hidden_denied(workspace, policy):
    """Even though edit reads + writes, hidden files must reject before
    any byte is touched."""
    with pytest.raises(ToolDenied, match="not visible to the agent"):
        safe_edit("evaluate.py", "HIDDEN", "OWNED",
                  policy=policy, workspace=workspace)
    assert "HIDDEN_BENCHMARK" in (workspace / "evaluate.py").read_text()


def test_safe_edit_readonly_denied(workspace, policy):
    """Read+write requires editable; readonly is rejected."""
    with pytest.raises(ToolDenied, match="read-only"):
        safe_edit("README.md", "Project", "TAMPERED",
                  policy=policy, workspace=workspace)


# ---------------------------------------------------------------------------
# safe_glob — visibility-filtered listing
# ---------------------------------------------------------------------------


def test_safe_glob_includes_visible(workspace, policy):
    out = safe_glob("*.py", policy=policy, workspace=workspace)
    assert "train.py" in out
    assert "helper.py" in out


def test_safe_glob_excludes_hidden(workspace, policy):
    out = safe_glob("*.py", policy=policy, workspace=workspace)
    assert "evaluate.py" not in out  # hidden, must be filtered out silently


def test_safe_glob_excludes_unlisted(workspace, policy):
    out = safe_glob("*.txt", policy=policy, workspace=workspace)
    assert "secret.txt" not in out  # unlisted


def test_safe_glob_rejects_traversal_pattern(workspace, policy):
    with pytest.raises(ToolDenied, match="parent-dir traversal"):
        safe_glob("../*", policy=policy, workspace=workspace)


def test_safe_glob_rejects_traversal_in_middle(workspace, policy):
    with pytest.raises(ToolDenied, match="parent-dir traversal"):
        safe_glob("subdir/../*", policy=policy, workspace=workspace)


def test_safe_glob_rejects_empty_pattern(workspace, policy):
    with pytest.raises(ToolDenied, match="non-empty string"):
        safe_glob("", policy=policy, workspace=workspace)


# ---------------------------------------------------------------------------
# safe_grep — visibility-filtered search
# ---------------------------------------------------------------------------


def test_safe_grep_finds_in_visible_files(workspace, policy):
    out = safe_grep("def", policy=policy, workspace=workspace)
    paths = {line.split(":")[0] for line in out}
    assert "train.py" in paths
    assert "helper.py" in paths


def test_safe_grep_skips_hidden_files(workspace, policy):
    """Even if the agent passes evaluate.py explicitly, grep must skip it."""
    out = safe_grep(
        "HIDDEN_BENCHMARK",
        files=["evaluate.py"],
        policy=policy,
        workspace=workspace,
    )
    assert out == []  # nothing returned — hidden file was silently skipped


def test_safe_grep_skips_unlisted_files(workspace, policy):
    out = safe_grep(
        "secret",
        files=["secret.txt"],
        policy=policy,
        workspace=workspace,
    )
    assert out == []


def test_safe_grep_rejects_invalid_regex(workspace, policy):
    with pytest.raises(ToolDenied, match="invalid regex"):
        safe_grep("(unclosed", policy=policy, workspace=workspace)


def test_safe_grep_rejects_empty_pattern(workspace, policy):
    with pytest.raises(ToolDenied, match="non-empty string"):
        safe_grep("", policy=policy, workspace=workspace)


def test_safe_grep_caps_at_limit(workspace, policy):
    """Adding many matches must cap at the configured limit."""
    big = "\n".join(f"line {i} match" for i in range(500))
    (workspace / "train.py").write_text(big)
    out = safe_grep("match", policy=policy, workspace=workspace, limit=10)
    assert len(out) == 10
