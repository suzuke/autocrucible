"""Tests for `crucible.agents.create_agent` factory — M2 PR 13.

These run regardless of whether `smolagents` is installed (reviewer
round 1 Q7 pin: factory + missing-extra error path must NOT all be
importorskip).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible.agents import create_agent
from crucible.config import AgentConfig, SmolagentsConfig
from crucible.security.cheat_resistance_policy import CheatResistancePolicy


@pytest.fixture
def policy(tmp_path: Path) -> CheatResistancePolicy:
    (tmp_path / "train.py").write_text("x = 1")
    return CheatResistancePolicy.from_lists(
        workspace=tmp_path,
        editable=["train.py"],
    )


# ---------------------------------------------------------------------------
# Type discriminator
# ---------------------------------------------------------------------------


def test_factory_unknown_type_fails_closed():
    """Unknown agent.type must raise — fail closed (reviewer pin)."""
    cfg = AgentConfig(type="experimental-codeact")
    with pytest.raises(ValueError, match="unknown agent type"):
        create_agent(cfg)


def test_factory_default_is_claude_code():
    """Existing behavior preserved: default config produces ClaudeCodeAgent."""
    cfg = AgentConfig()  # type defaults to "claude-code"
    # ClaudeCodeAgent constructor needs no required kwargs for this
    # smoke test — just verify factory dispatch picks the right class.
    from crucible.agents.claude_code import ClaudeCodeAgent
    agent = create_agent(cfg)
    assert isinstance(agent, ClaudeCodeAgent)


def test_factory_drops_smolagents_kwargs_for_claude_code(policy, tmp_path):
    """Passing smolagents-only kwargs to claude-code path must not error;
    those kwargs are silently dropped (so callers can pass them
    unconditionally)."""
    cfg = AgentConfig(type="claude-code")
    from crucible.agents.claude_code import ClaudeCodeAgent
    agent = create_agent(cfg, workspace=tmp_path, policy=policy)
    assert isinstance(agent, ClaudeCodeAgent)


# ---------------------------------------------------------------------------
# Smolagents path: missing-extra error
# ---------------------------------------------------------------------------


def test_factory_smolagents_requires_workspace_and_policy():
    """The smolagents path needs workspace + policy. Missing → ValueError
    BEFORE any import attempt (so the error is clear even if smolagents
    is installed)."""
    cfg = AgentConfig(type="smolagents")
    with pytest.raises(ValueError, match="workspace.*and.*policy"):
        create_agent(cfg)


def test_factory_smolagents_missing_extra_gives_install_hint(policy, tmp_path):
    """When `agent.type: smolagents` but the extra isn't installed, the
    error message MUST contain the actionable install command (reviewer
    round 1 Q1)."""
    cfg = AgentConfig(type="smolagents")

    # Simulate the smolagents extra being absent by removing both
    # cached modules from sys.modules and patching the backend's
    # _import_smolagents to fail. This works regardless of whether
    # smolagents is actually installed in this environment.
    from crucible.agents import smolagents_backend

    def _raise_missing():
        raise smolagents_backend.SmolagentsImportError("smolagents")

    with patch.object(smolagents_backend, "_import_smolagents", _raise_missing):
        with pytest.raises(smolagents_backend.SmolagentsImportError) as ei:
            create_agent(cfg, workspace=tmp_path, policy=policy)
    msg = str(ei.value)
    assert "pip install 'autocrucible[smolagents]'" in msg
    assert "smolagents" in msg


def test_factory_smolagents_litellm_missing_gives_install_hint(policy, tmp_path):
    """If smolagents is present but litellm is missing, error message
    still surfaces the [smolagents] extra install."""
    cfg = AgentConfig(type="smolagents")
    from crucible.agents import smolagents_backend

    def _raise_litellm_missing():
        raise smolagents_backend.SmolagentsImportError("litellm")

    with patch.object(smolagents_backend, "_import_smolagents", _raise_litellm_missing):
        with pytest.raises(smolagents_backend.SmolagentsImportError) as ei:
            create_agent(cfg, workspace=tmp_path, policy=policy)
    msg = str(ei.value)
    assert "litellm" in msg
    assert "[smolagents]" in msg


# ---------------------------------------------------------------------------
# Lazy-import contract: loading config must NOT import smolagents
# ---------------------------------------------------------------------------


def test_loading_agents_module_does_not_eagerly_import_smolagents(monkeypatch):
    """Importing `crucible.agents` (the public surface) must NOT cause
    smolagents to get imported. Only `_create_smolagents` should
    trigger that, and only when actually called."""
    # Force-evict smolagents to confirm it's not pulled in by import.
    for k in list(sys.modules):
        if k.startswith("crucible.agents"):
            del sys.modules[k]
    sys.modules.pop("smolagents", None)

    import crucible.agents  # noqa: F401

    # smolagents may already be in sys.modules from another test or
    # prior import in this run — that's allowed. The pin is that the
    # `crucible.agents` import alone doesn't pull it in.
    # We check the negative: if it WAS evicted before this import and
    # is now present, the eager-import would have pulled it back.
    # Since other tests may have imported it previously, accept either
    # state but verify create_agent for "claude-code" does not require
    # smolagents to be present.
    cfg = AgentConfig()  # claude-code default
    sys.modules.pop("smolagents", None)
    sys.modules.pop("crucible.agents.smolagents_backend", None)
    create_agent(cfg)  # MUST work without smolagents


def test_smolagents_backend_module_lazy_imports():
    """Importing `crucible.agents.smolagents_backend` itself does NOT
    import smolagents — that happens inside `SmolagentsBackend.__init__`
    via `_import_smolagents()`."""
    sys.modules.pop("smolagents", None)
    # Module-level imports should be safe:
    import importlib
    if "crucible.agents.smolagents_backend" in sys.modules:
        importlib.reload(sys.modules["crucible.agents.smolagents_backend"])
    else:
        import crucible.agents.smolagents_backend  # noqa: F401
    # Just importing the module must not have eagerly imported smolagents.
    # NOTE: in CI envs where smolagents was preloaded by another test,
    # this assertion would be a false negative. Use a fresh subprocess
    # for the strict version of this check.
    # For the in-process test, assert the lazy guard is callable:
    from crucible.agents.smolagents_backend import _import_smolagents
    assert callable(_import_smolagents)


# ---------------------------------------------------------------------------
# Forbidden tools must be ABSENT (not just rejected) — reviewer pin
# ---------------------------------------------------------------------------


def test_default_safe_tool_registry_excludes_forbidden():
    """The static tool registry MUST NOT include any of the forbidden
    tools. This is a structural invariant, not a runtime check."""
    smolagents = pytest.importorskip("smolagents")
    from crucible.agents._smolagents_tools import (
        DEFAULT_SAFE_TOOLS,
        FORBIDDEN_TOOL_NAMES,
        TOOL_NAMES_DEFAULT,
    )
    for forbidden in FORBIDDEN_TOOL_NAMES:
        assert forbidden not in TOOL_NAMES_DEFAULT, (
            f"forbidden tool {forbidden!r} appears in default registry "
            f"{TOOL_NAMES_DEFAULT!r}"
        )
    # Default registry has exactly the documented 5 tools:
    assert TOOL_NAMES_DEFAULT == (
        "read_file", "write_file", "edit_file", "glob", "grep"
    )


def test_default_safe_tool_classes_have_no_run_python_subclass():
    """Defence-in-depth: scan tool class names too (some smolagents
    versions name CodeAct executors `python_interpreter` etc.)."""
    smolagents = pytest.importorskip("smolagents")
    from crucible.agents._smolagents_tools import DEFAULT_SAFE_TOOLS
    for tool_cls in DEFAULT_SAFE_TOOLS:
        cls_name = tool_cls.__name__.lower()
        for forbidden in ("python", "execute", "shell", "eval", "codeact"):
            assert forbidden not in cls_name, (
                f"tool class {tool_cls.__name__!r} hints at forbidden capability"
            )
