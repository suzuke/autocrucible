"""POC Day 1 — Hypothesis property-based tests for `CheatResistancePolicy`.

These tests assert structural invariants that must hold for ANY input, not just
hand-picked attack cases:

  P1: classify() always returns one of the 4 declared classifications
  P2: classify() never raises (paths are validated, not trusted)
  P3: classify() is deterministic — same input → same output
  P4: any path resolving outside workspace → unlisted
  P5: case/separator/encoding mutations cannot upgrade hidden → editable
  P6: a fresh symlink to .env (hidden) is always classified hidden
  P7: a fresh hardlink to evaluate.py (readonly) is always classified readonly
  P8: is_writable_by_agent ⊂ is_visible_to_agent (writable implies visible)

If any of these break, the SSOT abstraction has bugs that no amount of
hand-written boundary tests will catch.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from crucible.security import CheatResistancePolicy


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate path components — letters, digits, dots, slashes, dashes, no nulls
_path_chars = st.text(
    alphabet=string.ascii_letters + string.digits + ".-_/" + "..",
    min_size=0,
    max_size=40,
).filter(lambda s: "\x00" not in s)

# Path mutations: prefixes that try to escape, normalize, or shadow
_escape_prefixes = st.sampled_from(
    [
        "",
        "./",
        "../",
        "../../",
        "subdir/../",
        "./solution.py/../",
        "/",
        "//",
        "/etc/",
        "/tmp/",
    ]
)

# All valid classifications
ALL_CLASSIFICATIONS = {"editable", "readonly", "hidden", "unlisted"}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "solution.py").write_text("# editable")
    (tmp_path / "evaluate.py").write_text("# readonly")
    (tmp_path / ".env").write_text("SECRET=42")
    (tmp_path / "subdir").mkdir(exist_ok=True)
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
# P1+P2: classify() always returns valid classification, never raises
# ---------------------------------------------------------------------------


@given(prefix=_escape_prefixes, name=_path_chars)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_classify_always_returns_valid_label(policy, prefix, name):
    path_str = prefix + name
    cls = policy.classify(path_str)
    assert cls in ALL_CLASSIFICATIONS


@given(prefix=_escape_prefixes, name=_path_chars)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_classify_never_raises(policy, prefix, name):
    """No matter how weird the input, classify() must not raise."""
    path_str = prefix + name
    # Should not raise — even on totally absurd input.
    policy.classify(path_str)


# ---------------------------------------------------------------------------
# P3: determinism
# ---------------------------------------------------------------------------


@given(prefix=_escape_prefixes, name=_path_chars)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_classify_is_deterministic(policy, prefix, name):
    path_str = prefix + name
    a = policy.classify(path_str)
    b = policy.classify(path_str)
    assert a == b


# ---------------------------------------------------------------------------
# P4: anything resolving outside workspace is unlisted
# ---------------------------------------------------------------------------


@given(prefix=st.sampled_from(["../", "../../", "../../../", "/etc/", "/tmp/", "/var/"]),
       name=_path_chars)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_outside_workspace_is_unlisted(policy, workspace, prefix, name):
    """Paths that resolve to outside the workspace must be 'unlisted'."""
    path_str = prefix + name
    # If the resolved path happens to fall back inside the workspace
    # (because of weird `../subdir/../foo` shenanigans), skip — we're
    # only testing the property for paths that escape.
    resolved = (workspace / path_str).resolve(strict=False) if not path_str.startswith("/") else Path(path_str).resolve(strict=False)
    try:
        resolved.relative_to(workspace.resolve())
        return  # path didn't actually escape, skip
    except ValueError:
        pass
    cls = policy.classify(path_str)
    assert cls == "unlisted", f"escaping path {path_str!r} → {resolved} classified as {cls}"


# ---------------------------------------------------------------------------
# P5: mutations cannot upgrade hidden → editable
# ---------------------------------------------------------------------------


@given(separator=st.sampled_from(["/", "//", "///"]),
       prefix=st.sampled_from(["", "./", ".//"]))
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_path_normalization_cannot_upgrade_hidden(policy, workspace, separator, prefix):
    """Any equivalent reference to .env (via different separators / prefixes)
    must still be classified hidden, never editable or readonly."""
    candidates = [
        prefix + ".env",
        prefix + separator + ".env",
        prefix + "subdir" + separator + ".." + separator + ".env",
        prefix + "./.env",
    ]
    for c in candidates:
        cls = policy.classify(workspace / c)
        # Either hidden (correctly identified) or unlisted (path didn't resolve to .env).
        # Must NEVER be editable or readonly.
        assert cls in ("hidden", "unlisted"), f"{c!r} → {cls} (LEAK)"


# ---------------------------------------------------------------------------
# P6: any new symlink pointing to a hidden file is hidden
# ---------------------------------------------------------------------------


@given(name=st.text(alphabet=string.ascii_letters + "_-", min_size=3, max_size=20))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symlink_to_hidden_always_hidden(policy, workspace, name):
    """No matter what name an attacker chooses, a symlink pointing to .env
    must be classified hidden. Otherwise we'd leak via `agent_decoy.py -> .env`.
    """
    link_path = workspace / f"{name}.py"
    if link_path.exists() or link_path.is_symlink():
        return  # skip collisions
    link_path.symlink_to(workspace / ".env")
    try:
        cls = policy.classify(link_path)
        assert cls == "hidden", f"{link_path.name} symlinked to .env → {cls} (LEAK)"
    finally:
        if link_path.is_symlink():
            link_path.unlink()


# ---------------------------------------------------------------------------
# P7: any new hardlink to readonly is readonly
# ---------------------------------------------------------------------------


@given(name=st.text(alphabet=string.ascii_letters + "_-", min_size=3, max_size=20))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_hardlink_to_readonly_always_readonly(policy, workspace, name):
    link_path = workspace / f"{name}.py"
    if link_path.exists():
        return
    os.link(workspace / "evaluate.py", link_path)
    try:
        cls = policy.classify(link_path)
        assert cls == "readonly", f"{link_path.name} hardlinked to evaluate.py → {cls} (LEAK)"
    finally:
        if link_path.exists():
            link_path.unlink()


# ---------------------------------------------------------------------------
# P8: is_writable ⊂ is_visible (writable implies visible)
# ---------------------------------------------------------------------------


@given(prefix=_escape_prefixes, name=_path_chars)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_writable_implies_visible(policy, prefix, name):
    path_str = prefix + name
    if policy.is_writable_by_agent(path_str):
        assert policy.is_visible_to_agent(path_str), (
            f"{path_str!r} is writable but not visible — invariant broken"
        )


# ---------------------------------------------------------------------------
# P9: hidden never visible (the strongest property)
# ---------------------------------------------------------------------------


@given(prefix=_escape_prefixes, name=_path_chars)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_hidden_never_visible(policy, prefix, name):
    path_str = prefix + name
    if policy.classify(path_str) == "hidden":
        assert not policy.is_visible_to_agent(path_str), (
            f"{path_str!r} classified hidden but is_visible_to_agent returned True"
        )
