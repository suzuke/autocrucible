"""Tests for `crucible.agents._smolagents_tools` — M2 PR 13.

Skipped when smolagents isn't installed. Verifies:
  - Tool subclasses correctly delegate to safe_* helpers
  - ACL denial returns a structured `[denied] ...` string (not a raise)
  - Forbidden tools are not constructible / not in registry
  - Default tool list has exactly the 5 documented tools
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the entire module if smolagents isn't installed.
pytest.importorskip("smolagents")

from crucible.agents._smolagents_tools import (
    DEFAULT_SAFE_TOOLS,
    EditFileTool,
    FORBIDDEN_TOOL_NAMES,
    GlobTool,
    GrepTool,
    ReadFileTool,
    TOOL_NAMES_DEFAULT,
    WriteFileTool,
    build_default_tools,
)
from crucible.security.cheat_resistance_policy import CheatResistancePolicy


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "train.py").write_text("def train():\n    return 0.5\n")
    (tmp_path / "README.md").write_text("# project\n")
    (tmp_path / "evaluate.py").write_text("HIDDEN = 1\n")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> CheatResistancePolicy:
    return CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py"],
        readonly=["README.md"],
        hidden=["evaluate.py"],
    )


# ---------------------------------------------------------------------------
# Registry sanity (these don't need a running agent)
# ---------------------------------------------------------------------------


def test_default_registry_has_exactly_5_tools():
    assert len(DEFAULT_SAFE_TOOLS) == 5
    assert TOOL_NAMES_DEFAULT == (
        "read_file", "write_file", "edit_file", "glob", "grep"
    )


def test_forbidden_tool_names_are_documented():
    """Sanity check on the forbidden list itself."""
    assert "run_python" in FORBIDDEN_TOOL_NAMES
    assert "run_shell" in FORBIDDEN_TOOL_NAMES
    assert "eval_code" in FORBIDDEN_TOOL_NAMES
    assert "execute" in FORBIDDEN_TOOL_NAMES
    assert "python_interpreter" in FORBIDDEN_TOOL_NAMES
    assert "code_act" in FORBIDDEN_TOOL_NAMES


def test_build_default_tools_returns_5_instances(workspace, policy):
    tools = build_default_tools(policy=policy, workspace=workspace)
    assert len(tools) == 5
    names = [t.name for t in tools]
    assert names == ["read_file", "write_file", "edit_file", "glob", "grep"]


# ---------------------------------------------------------------------------
# ReadFileTool — ACL boundary
# ---------------------------------------------------------------------------


def test_read_tool_editable_ok(workspace, policy):
    tool = ReadFileTool(policy=policy, workspace=workspace)
    out = tool.forward("train.py")
    assert "def train" in out


def test_read_tool_hidden_returns_denial_string(workspace, policy):
    """The agent should see `[denied] ...` rather than an exception
    (smolagents agent loop handles strings)."""
    tool = ReadFileTool(policy=policy, workspace=workspace)
    out = tool.forward("evaluate.py")
    assert out.startswith("[denied]")
    assert "not visible to the agent" in out
    # Critically: the actual file content (HIDDEN = 1) MUST NOT leak.
    assert "HIDDEN" not in out


def test_read_tool_readonly_ok(workspace, policy):
    tool = ReadFileTool(policy=policy, workspace=workspace)
    assert "# project" in tool.forward("README.md")


def test_read_tool_traversal_denied(workspace, policy):
    tool = ReadFileTool(policy=policy, workspace=workspace)
    out = tool.forward("../etc/passwd")
    assert out.startswith("[denied]")
    assert "escapes workspace" in out


# ---------------------------------------------------------------------------
# WriteFileTool — ACL boundary
# ---------------------------------------------------------------------------


def test_write_tool_editable_ok(workspace, policy):
    tool = WriteFileTool(policy=policy, workspace=workspace)
    out = tool.forward("train.py", "new content")
    assert out.startswith("wrote")
    assert (workspace / "train.py").read_text() == "new content"


def test_write_tool_readonly_denied(workspace, policy):
    tool = WriteFileTool(policy=policy, workspace=workspace)
    out = tool.forward("README.md", "tampered")
    assert out.startswith("[denied]")
    assert "read-only" in out
    assert (workspace / "README.md").read_text() == "# project\n"


def test_write_tool_hidden_denied_does_not_leak_path(workspace, policy):
    """Critical: the agent must not be able to overwrite evaluate.py and
    must not learn the file exists from the error."""
    tool = WriteFileTool(policy=policy, workspace=workspace)
    out = tool.forward("evaluate.py", "PWNED = 1\n")
    assert out.startswith("[denied]")
    assert "not visible to the agent" in out
    assert "HIDDEN" in (workspace / "evaluate.py").read_text()  # unchanged


# ---------------------------------------------------------------------------
# EditFileTool — search/replace contract
# ---------------------------------------------------------------------------


def test_edit_tool_unique_replace_ok(workspace, policy):
    tool = EditFileTool(policy=policy, workspace=workspace)
    out = tool.forward("train.py", "return 0.5", "return 0.7")
    assert out.startswith("edited")
    assert "return 0.7" in (workspace / "train.py").read_text()


def test_edit_tool_ambiguous_replace_denied(workspace, policy):
    (workspace / "train.py").write_text("x = 1\nx = 1\n")
    tool = EditFileTool(policy=policy, workspace=workspace)
    out = tool.forward("train.py", "x = 1", "x = 2")
    assert out.startswith("[denied]")
    assert "ambiguous" in out


def test_edit_tool_hidden_denied(workspace, policy):
    tool = EditFileTool(policy=policy, workspace=workspace)
    out = tool.forward("evaluate.py", "HIDDEN", "OWNED")
    assert out.startswith("[denied]")
    assert "HIDDEN" in (workspace / "evaluate.py").read_text()


# ---------------------------------------------------------------------------
# GlobTool — visibility filter
# ---------------------------------------------------------------------------


def test_glob_tool_excludes_hidden(workspace, policy):
    tool = GlobTool(policy=policy, workspace=workspace)
    out = tool.forward("*.py")
    # train.py is editable → visible
    assert "train.py" in out
    # evaluate.py is hidden → MUST NOT appear
    assert "evaluate.py" not in out


def test_glob_tool_no_matches_message(workspace, policy):
    tool = GlobTool(policy=policy, workspace=workspace)
    out = tool.forward("*.nonexistent_ext")
    assert out == "(no matches)"


# ---------------------------------------------------------------------------
# GrepTool — visibility filter
# ---------------------------------------------------------------------------


def test_grep_tool_finds_in_visible(workspace, policy):
    tool = GrepTool(policy=policy, workspace=workspace)
    out = tool.forward("def")
    assert "train.py" in out


def test_grep_tool_skips_hidden_explicitly_passed(workspace, policy):
    """Even with explicit `files=evaluate.py`, hidden file is skipped."""
    tool = GrepTool(policy=policy, workspace=workspace)
    out = tool.forward("HIDDEN", "evaluate.py")
    assert "(no matches)" in out  # silent skip
    # The actual hidden value must NOT be in output.
    assert "= 1" not in out
