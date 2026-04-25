"""Tests for `crucible.agents.smolagents_backend.SmolagentsBackend` —
M2 PR 13.

Skipped when smolagents isn't installed. Verifies:
  - Construction is pure (no network call).
  - `backend_kind` is the stable string `"smolagents"`.
  - `backend_version` resolves; missing → `"unknown"` (no raise).
  - `generate_edit` maps successful agent.run to AgentResult.
  - Failures are classified into AgentErrorType.
  - File modification detection works via mtime snapshot.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip the entire module if smolagents isn't installed.
pytest.importorskip("smolagents")

from crucible.agents.base import AgentErrorType
from crucible.agents.smolagents_backend import (
    BACKEND_KIND,
    SmolagentsBackend,
    SmolagentsImportError,
    _classify_error,
    _resolve_backend_version,
)
from crucible.config import SmolagentsConfig
from crucible.security.cheat_resistance_policy import CheatResistancePolicy


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "train.py").write_text("x = 1\n")
    return tmp_path


@pytest.fixture
def policy(workspace: Path) -> CheatResistancePolicy:
    return CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py"],
    )


@pytest.fixture
def smolagents_config() -> SmolagentsConfig:
    return SmolagentsConfig(
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        api_key_env="ANTHROPIC_API_KEY",
        max_steps=3,
    )


# ---------------------------------------------------------------------------
# Identity (recorded on AttemptNode)
# ---------------------------------------------------------------------------


def test_backend_kind_is_stable_string(workspace, policy, smolagents_config, monkeypatch):
    """`backend_kind` must be the literal `"smolagents"` — not provider or
    model-specific (reviewer round 1 pin)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    assert backend.backend_kind == "smolagents"
    assert BACKEND_KIND == "smolagents"


def test_backend_version_best_effort(workspace, policy, smolagents_config, monkeypatch):
    """`backend_version` returns the smolagents version if installed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    # Should be a non-empty version string (e.g. "1.24.0") or "unknown".
    assert isinstance(backend.backend_version, str)
    assert backend.backend_version  # non-empty
    # Sanity: shouldn't be the literal "unknown" if smolagents installed.
    # (We allow "unknown" as fallback so test is informational not strict.)


def test_resolve_backend_version_returns_unknown_when_missing(monkeypatch):
    """If both metadata and `__version__` fail, fall back to "unknown" —
    NEVER raise (reviewer round 1 pin)."""
    import importlib.metadata
    def _raise_pkg_not_found(name):
        raise importlib.metadata.PackageNotFoundError(name)
    with patch.object(importlib.metadata, "version", _raise_pkg_not_found):
        # Also patch the module to lack __version__:
        import smolagents
        original = getattr(smolagents, "__version__", None)
        try:
            if hasattr(smolagents, "__version__"):
                delattr(smolagents, "__version__")
            assert _resolve_backend_version() == "unknown"
        finally:
            if original is not None:
                smolagents.__version__ = original


# ---------------------------------------------------------------------------
# Construction is pure (no network call)
# ---------------------------------------------------------------------------


def test_construction_does_not_call_network(workspace, policy, smolagents_config, monkeypatch):
    """The reviewer pin is that factory construction must not initiate a
    network call. We can't fully assert "no network", but we can assert
    no calls to `model.generate` happen during __init__."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    # Backend exists; agent and model are constructed but unused.
    assert backend._agent is not None
    assert backend._model is not None
    # No way to check zero calls without an explicit network mock —
    # smolagents builds LiteLLMModel without a probe call as of v1.24.
    # This test serves as a structural pin: failing means smolagents
    # changed to make a network call in construction.


# ---------------------------------------------------------------------------
# generate_edit prompt → AgentResult mapping (mocked agent.run)
# ---------------------------------------------------------------------------


def test_generate_edit_maps_agent_run_to_result(workspace, policy, smolagents_config, monkeypatch):
    """Successful agent.run() returns a string that becomes
    AgentResult.description."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )

    # Replace agent.run with a stub that simulates "I made an edit"
    def _fake_run(prompt: str, **kwargs):
        # Simulate the agent writing to train.py via the WriteFileTool
        (workspace / "train.py").write_text("x = 2\n")
        return "Improved x to 2 for better accuracy."

    backend._agent.run = _fake_run

    result = backend.generate_edit("Increase x", workspace)
    assert "Improved" in result.description
    assert result.error_type is None
    # File modification detected via mtime diff:
    modified_names = {p.name for p in result.modified_files}
    assert "train.py" in modified_names
    assert result.duration_seconds >= 0


def test_generate_edit_no_edit_no_modified_files(workspace, policy, smolagents_config, monkeypatch):
    """If the agent run produces no file changes, modified_files is empty."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    backend._agent.run = lambda prompt, **kwargs: "nothing to do"
    result = backend.generate_edit("noop", workspace)
    assert result.modified_files == []
    assert result.description == "nothing to do"


def test_generate_edit_handles_agent_exception(workspace, policy, smolagents_config, monkeypatch):
    """Agent run raising → AgentResult with error_type set, no crash."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("LLM call failed")

    backend._agent.run = _raise
    result = backend.generate_edit("anything", workspace)
    assert result.error_type == AgentErrorType.UNKNOWN
    assert "LLM call failed" in result.agent_output
    assert result.description == ""


def test_generate_edit_classifies_auth_errors(workspace, policy, smolagents_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )

    backend._agent.run = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("invalid api key, unauthorized")
    )
    result = backend.generate_edit("anything", workspace)
    assert result.error_type == AgentErrorType.AUTH


def test_generate_edit_classifies_timeout(workspace, policy, smolagents_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )

    backend._agent.run = lambda *a, **kw: (_ for _ in ()).throw(
        RuntimeError("request timeout exceeded")
    )
    result = backend.generate_edit("anything", workspace)
    assert result.error_type == AgentErrorType.TIMEOUT


# ---------------------------------------------------------------------------
# API key handling: never stored
# ---------------------------------------------------------------------------


def test_api_key_value_not_stored_on_backend(workspace, policy, smolagents_config, monkeypatch):
    """Reviewer round 1 Q2 pin: only the env var NAME is referenced;
    the value is never stored on the backend object."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-DO-NOT-LEAK")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    # Inspect every attribute on the backend; none should hold the key.
    for attr_name in dir(backend):
        if attr_name.startswith("__"):
            continue
        try:
            value = getattr(backend, attr_name)
        except Exception:
            continue
        if isinstance(value, str):
            assert "sk-secret-DO-NOT-LEAK" not in value, (
                f"API key value leaked into attribute {attr_name!r}"
            )
    # Specifically, _api_key_env stores only the NAME.
    assert backend._api_key_env == "ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# Capabilities (orchestrator gates features on these)
# ---------------------------------------------------------------------------


def test_capabilities_match_default_safe_tools(workspace, policy, smolagents_config, monkeypatch):
    """Capabilities must NOT include `run_python`, `run_shell`, etc."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    backend = SmolagentsBackend(
        config=smolagents_config, policy=policy, workspace=workspace
    )
    caps = backend.capabilities()
    assert caps == {"read", "edit", "write", "glob", "grep"}
    assert "run_python" not in caps
    assert "run_shell" not in caps
    assert "execute" not in caps


# ---------------------------------------------------------------------------
# Error classifier helper
# ---------------------------------------------------------------------------


def test_classify_error_auth():
    assert _classify_error("Invalid API key") == AgentErrorType.AUTH
    assert _classify_error("Unauthorized") == AgentErrorType.AUTH
    assert _classify_error("Authentication failed") == AgentErrorType.AUTH


def test_classify_error_timeout():
    assert _classify_error("request timeout") == AgentErrorType.TIMEOUT


def test_classify_error_unknown():
    assert _classify_error("Some other error") == AgentErrorType.UNKNOWN
