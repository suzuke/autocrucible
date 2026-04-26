"""Tests for `crucible.agents.cli_subscription` — M3 PR 16.

Coverage matrix per reviewer rounds 1+2:
  - Q7 secret redaction: argv tokens + env names
  - Q5 subprocess mgmt: timeout kill, stdout-cap kill (no zombies)
  - Q4 stream-json schema-version guard
  - Q2 compliance gate freshness check
  - Q6 tri-state safety filter detection
  - Q8 two-flag opt-in + scratch dir + isolation tag
  - Reviewer scope-conformance: stub adapters raise AdapterNotImplementedError
  - Backend factory dispatch + capabilities

Tests that need an actual `claude` binary use `pytest.importorskip` /
shutil.which guards. Most logic is testable with mocked subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crucible.agents.cli_subscription.compliance import (
    ADMIT_THRESHOLD,
    COMPLIANCE_FRESHNESS,
    RELEASE_THRESHOLD,
    BenignTask,
    ComplianceReport,
    TrialClassification,
    TrialResult,
    load_reports,
    persist_report,
    reports_dir_for,
    verify_recent_pass,
)
from crucible.agents.cli_subscription.redaction import (
    REDACTED,
    is_secret_env_name,
    redact_argv,
    redact_env,
)
from crucible.agents.cli_subscription.safety import (
    SafetyDetection,
    SafetyFilterState,
    detect_safety_filter,
)
from crucible.agents.cli_subscription.scratch import (
    cli_scratch_dir,
    copy_editable_changes_back,
)


# ---------------------------------------------------------------------------
# Q7 — Secret redaction
# ---------------------------------------------------------------------------


def test_redact_argv_password_equals_form():
    out = redact_argv(["claude", "--password=hunter2"])
    assert out == ["claude", f"--password={REDACTED}"]


def test_redact_argv_password_separate_form():
    out = redact_argv(["claude", "--password", "hunter2"])
    assert out == ["claude", "--password", REDACTED]


def test_redact_argv_api_key():
    out = redact_argv(["claude", "--api-key", "sk-real-secret"])
    assert "sk-real-secret" not in out
    assert REDACTED in out


def test_redact_argv_token_equals():
    out = redact_argv(["claude", "--token=ghp_xxx"])
    assert "ghp_xxx" not in " ".join(out)


def test_redact_argv_does_not_redact_dash_p():
    """Reviewer round 2 Bug #1 regression: `-p` is the PROMPT flag in
    Claude Code CLI (and `--print` short form in many others). Including
    it in the redaction heuristic destroyed the prompt in every recorded
    `cli_argv`. The fix removes `-p` from `_SECRET_FLAG_NAMES`. This
    test asserts the prompt is preserved verbatim."""
    out = redact_argv(["claude", "-p", "Please optimize solution.py"])
    assert out == ["claude", "-p", "Please optimize solution.py"]


def test_redact_argv_passes_through_safe_args():
    out = redact_argv(["claude", "-p", "hunter2", "--print", "--output-format", "json"])
    # The safe non-secret flags survive intact
    assert "--print" in out
    assert "--output-format" in out
    assert "json" in out
    # `-p` is no longer redacted (Bug #1 fix); "hunter2" is a benign
    # prompt argument here, not a password.
    assert "hunter2" in out


def test_claude_code_cli_argv_preserves_prompt_through_redaction(monkeypatch):
    """Reviewer round 2 Bug #1: end-to-end regression. The prompt the
    user typed must survive `build_argv` -> `redact_argv` round-trip
    intact, otherwise observability is destroyed."""
    from pathlib import Path
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription.base import AdapterRunContext

    monkeypatch.setattr(
        ClaudeCodeCLIAdapter, "_resolve_binary",
        lambda self, p: Path("/fake/claude"),
    )
    monkeypatch.setattr(
        ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0"
    )
    adapter = ClaudeCodeCLIAdapter()

    user_prompt = "Please optimize solution.py for tokenizer compression"
    ctx = AdapterRunContext(
        prompt=user_prompt,
        scratch_dir=Path("/tmp/scratch"),
        workspace_root=Path("/tmp/scratch"),
        timeout_seconds=600,
        stdout_cap_bytes=10 * 1024 * 1024,
    )
    raw_argv = list(adapter.build_argv(ctx))
    redacted = redact_argv(raw_argv)

    # The prompt MUST appear verbatim in the redacted argv
    assert user_prompt in redacted, (
        f"prompt was redacted out of cli_argv. Got: {redacted!r}"
    )
    # And no `<redacted>` token should appear (no secrets in this argv)
    assert REDACTED not in redacted


def test_redact_env_strips_secret_named_values():
    env = {
        "OPENAI_API_KEY": "sk-real",
        "GITHUB_TOKEN": "ghp_xxx",
        "MY_PASSWORD": "hunter2",
        "USER": "alice",
        "PATH": "/usr/bin",
    }
    out = redact_env(env)
    assert out["OPENAI_API_KEY"] == REDACTED
    assert out["GITHUB_TOKEN"] == REDACTED
    assert out["MY_PASSWORD"] == REDACTED
    # Non-secret names pass through
    assert out["USER"] == "alice"
    assert out["PATH"] == "/usr/bin"


def test_is_secret_env_name():
    assert is_secret_env_name("OPENAI_API_KEY") is True
    assert is_secret_env_name("GITHUB_TOKEN") is True
    assert is_secret_env_name("MY_PASSWORD") is True
    assert is_secret_env_name("AUTH_BEARER") is True
    assert is_secret_env_name("USER") is False
    assert is_secret_env_name("HOME") is False


# ---------------------------------------------------------------------------
# Q6 — Tri-state safety filter detection
# ---------------------------------------------------------------------------


def test_safety_detected_via_structured_event_tool_use_denied():
    events = [{"type": "tool_use_denied", "tool": "read_file"}]
    out = detect_safety_filter(
        adapter="claude-code-cli",
        stdout_text="",
        structured_events=events,
    )
    assert out.state == SafetyFilterState.DETECTED
    assert out.source == "structured_event"
    assert "tool_use_denied" in out.evidence


def test_safety_detected_via_stop_reason_refusal():
    events = [{"stop_reason": "refusal"}]
    out = detect_safety_filter(
        adapter="claude-code-cli",
        stdout_text="",
        structured_events=events,
    )
    assert out.state == SafetyFilterState.DETECTED
    assert "refusal" in out.evidence


def test_safety_detected_via_phrase_heuristic():
    out = detect_safety_filter(
        adapter="claude-code-cli",
        stdout_text="I cannot help with that request.",
    )
    assert out.state == SafetyFilterState.DETECTED
    assert out.source == "phrase"


def test_safety_not_detected_when_tool_was_called():
    """Strong evidence the provider didn't block: tool was invoked
    AND no refusal phrase matched."""
    out = detect_safety_filter(
        adapter="claude-code-cli",
        stdout_text="Reading file solution.py...\nDone.",
        tool_was_called=True,
    )
    assert out.state == SafetyFilterState.NOT_DETECTED


def test_safety_unknown_when_insufficient_signal():
    """Reviewer Q6 critical: don't coerce unknown to false."""
    out = detect_safety_filter(
        adapter="claude-code-cli",
        stdout_text="ambiguous output without phrase or tool",
    )
    assert out.state == SafetyFilterState.UNKNOWN
    assert out.source == ""


def test_safety_unknown_with_no_inputs():
    out = detect_safety_filter(adapter="claude-code-cli", stdout_text="")
    assert out.state == SafetyFilterState.UNKNOWN


# ---------------------------------------------------------------------------
# Q4 — stream-json schema_version guard
# ---------------------------------------------------------------------------


def test_claude_code_cli_known_schema_parses_ok(monkeypatch):
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription.base import AdapterRawResult

    # Monkeypatch resolution + version so test doesn't need real binary
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")
    adapter = ClaudeCodeCLIAdapter()

    raw = AdapterRawResult(
        argv_redacted=["claude", "-p", "x"],
        stdout=(
            json.dumps({"type": "assistant_message", "content": "hello", "schema_version": "1"}) + "\n"
            + json.dumps({"type": "tool_use", "tool": "read_file"}) + "\n"
        ),
        stderr_tail="",
        exit_code=0,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.1,
    )
    parsed = adapter.parse_output(raw)
    assert parsed.unknown_schema is False
    assert "hello" in parsed.description
    assert parsed.tool_was_called is True


def test_claude_code_cli_unknown_schema_classified_as_parse_failure(monkeypatch):
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription.base import AdapterRawResult

    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "999.0.0")
    adapter = ClaudeCodeCLIAdapter()

    raw = AdapterRawResult(
        argv_redacted=["claude"],
        stdout=json.dumps({
            "type": "assistant_message",
            "content": "hi",
            "schema_version": "9999",  # unknown
        }) + "\n",
        stderr_tail="",
        exit_code=0,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.1,
    )
    parsed = adapter.parse_output(raw)
    assert parsed.unknown_schema is True


def test_claude_code_cli_non_json_lines_mark_unknown_schema(monkeypatch):
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription.base import AdapterRawResult

    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")
    adapter = ClaudeCodeCLIAdapter()

    raw = AdapterRawResult(
        argv_redacted=["claude"],
        stdout="this is plain text not JSON\nstill not json\n",
        stderr_tail="",
        exit_code=0,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.1,
    )
    parsed = adapter.parse_output(raw)
    assert parsed.unknown_schema is True


# ---------------------------------------------------------------------------
# Q1 — Stub adapters raise AdapterNotImplementedError
# ---------------------------------------------------------------------------


def test_codex_adapter_no_longer_a_stub(monkeypatch):
    """PR 16a landed CodexCLIAdapter; build_argv must succeed (not raise)."""
    from crucible.agents.cli_subscription.codex_cli import CodexCLIAdapter
    from crucible.agents.cli_subscription.base import AdapterRunContext

    monkeypatch.setattr(CodexCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/codex"))
    monkeypatch.setattr(CodexCLIAdapter, "_read_version", lambda self: "0.1.0")
    adapter = CodexCLIAdapter()

    ctx = AdapterRunContext(
        prompt="say hi",
        scratch_dir=Path("/tmp/scratch"),
        workspace_root=Path("/tmp/scratch"),
        timeout_seconds=30,
        stdout_cap_bytes=1_000_000,
    )
    argv = list(adapter.build_argv(ctx))
    assert argv[0] == "/fake/codex"
    assert "exec" in argv
    assert "--json" in argv


def test_gemini_adapter_no_longer_a_stub(monkeypatch):
    """PR 16b landed GeminiCLIAdapter; build_argv must succeed (not raise)."""
    from crucible.agents.cli_subscription.gemini_cli import GeminiCLIAdapter
    from crucible.agents.cli_subscription.base import AdapterRunContext

    monkeypatch.setattr(GeminiCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/gemini"))
    monkeypatch.setattr(GeminiCLIAdapter, "_read_version", lambda self: "0.39.1")
    adapter = GeminiCLIAdapter()

    ctx = AdapterRunContext(
        prompt="say hi",
        scratch_dir=Path("/tmp/scratch"),
        workspace_root=Path("/tmp/scratch"),
        timeout_seconds=30,
        stdout_cap_bytes=1_000_000,
    )
    argv = list(adapter.build_argv(ctx))
    assert argv[0] == "/fake/gemini"
    assert "-p" in argv
    assert "stream-json" in argv


# ---------------------------------------------------------------------------
# Compliance harness (Q2 — gate enforced not advisory)
# ---------------------------------------------------------------------------


@pytest.fixture
def compliance_dir(tmp_path):
    return tmp_path / "compliance-reports"


def _make_report(
    adapter: str,
    binary: str,
    version: str,
    pass_count: int,
    total: int,
    started_at: str,
) -> ComplianceReport:
    trials = [
        TrialResult(
            task_id=f"t{i}",
            classification=(
                TrialClassification.PARSE_SUCCESS if i < pass_count
                else TrialClassification.PARSE_FAILURE
            ),
        )
        for i in range(total)
    ]
    return ComplianceReport(
        adapter=adapter,
        cli_binary_path=binary,
        cli_version=version,
        started_at=started_at,
        ended_at=started_at,
        trials=trials,
    )


def test_compliance_report_persist_load_roundtrip(compliance_dir):
    rpt = _make_report(
        "claude-code-cli", "/usr/bin/claude", "1.0.0", 99, 100,
        "2026-04-26T01:00:00Z",
    )
    persist_report(rpt, dest_dir=compliance_dir)
    loaded = load_reports(compliance_dir)
    assert len(loaded) == 1
    r = loaded[0]
    assert r.adapter == "claude-code-cli"
    assert r.cli_version == "1.0.0"
    assert r.passes == 99
    assert r.pass_rate == 0.99


def test_verify_recent_pass_returns_passing_report(compliance_dir):
    now = datetime(2026, 4, 26, 1, 0, 0, tzinfo=timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/usr/bin/claude", "1.0.0", 99, 100,
        (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
    )
    persist_report(rpt, dest_dir=compliance_dir)

    out = verify_recent_pass(
        adapter="claude-code-cli",
        cli_binary_path="/usr/bin/claude",
        cli_version="1.0.0",
        reports_dir=compliance_dir,
        threshold=0.99,
        now=now,
    )
    assert out is not None
    assert out.pass_rate == 0.99


def test_verify_recent_pass_rejects_stale_report(compliance_dir):
    """Reviewer Q2: report older than COMPLIANCE_FRESHNESS doesn't count."""
    now = datetime(2026, 4, 26, 1, 0, 0, tzinfo=timezone.utc)
    stale = (now - COMPLIANCE_FRESHNESS - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    rpt = _make_report(
        "claude-code-cli", "/usr/bin/claude", "1.0.0", 99, 100, stale,
    )
    persist_report(rpt, dest_dir=compliance_dir)

    out = verify_recent_pass(
        adapter="claude-code-cli",
        cli_binary_path="/usr/bin/claude",
        cli_version="1.0.0",
        reports_dir=compliance_dir,
        now=now,
    )
    assert out is None


def test_verify_recent_pass_rejects_different_cli_version(compliance_dir):
    """Reviewer Q3: cli_version mismatch invalidates the report."""
    now = datetime(2026, 4, 26, 1, 0, 0, tzinfo=timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/usr/bin/claude", "1.0.0", 99, 100,
        (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
    )
    persist_report(rpt, dest_dir=compliance_dir)

    # Same binary path but different version (e.g. brew upgrade)
    out = verify_recent_pass(
        adapter="claude-code-cli",
        cli_binary_path="/usr/bin/claude",
        cli_version="2.0.0",
        reports_dir=compliance_dir,
        now=now,
    )
    assert out is None


def test_verify_recent_pass_rejects_below_threshold(compliance_dir):
    """Pass-rate below threshold → no admit."""
    now = datetime(2026, 4, 26, 1, 0, 0, tzinfo=timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/usr/bin/claude", "1.0.0", 90, 100,  # 90% < 99%
        (now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
    )
    persist_report(rpt, dest_dir=compliance_dir)

    out = verify_recent_pass(
        adapter="claude-code-cli",
        cli_binary_path="/usr/bin/claude",
        cli_version="1.0.0",
        reports_dir=compliance_dir,
        threshold=0.99,
        now=now,
    )
    assert out is None


# ---------------------------------------------------------------------------
# Q8 — Scratch dir
# ---------------------------------------------------------------------------


def test_scratch_dir_copies_only_visible_files(tmp_path):
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    workspace = tmp_path
    (workspace / "train.py").write_text("editable\n")
    (workspace / "README.md").write_text("readonly\n")
    (workspace / "evaluate.py").write_text("HIDDEN\n")
    (workspace / "secret.txt").write_text("unlisted\n")

    policy = CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py"],
        readonly=["README.md"],
        hidden=["evaluate.py"],
    )

    with cli_scratch_dir(workspace=workspace, policy=policy) as scratch:
        assert (scratch / "train.py").exists()
        assert (scratch / "README.md").exists()
        # Hidden file MUST NOT be in scratch
        assert not (scratch / "evaluate.py").exists()
        # Unlisted file MUST NOT be in scratch
        assert not (scratch / "secret.txt").exists()


def test_copy_editable_changes_back_only_for_editable(tmp_path):
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    workspace = tmp_path
    (workspace / "train.py").write_text("original\n")
    (workspace / "README.md").write_text("readonly\n")

    policy = CheatResistancePolicy.from_lists(
        workspace=workspace,
        editable=["train.py"],
        readonly=["README.md"],
    )

    with cli_scratch_dir(workspace=workspace, policy=policy) as scratch:
        # Simulate CLI mutating both files in scratch
        (scratch / "train.py").write_text("modified by CLI\n")
        (scratch / "README.md").write_text("ALSO MODIFIED — should NOT propagate\n")

        modified = copy_editable_changes_back(
            scratch=scratch, workspace=workspace, policy=policy,
        )

    # Editable change propagated
    assert (workspace / "train.py").read_text() == "modified by CLI\n"
    # Readonly change did NOT propagate (reviewer Q8: scratch is reproducibility,
    # but copy-back layer enforces editable-only mutation discipline)
    assert (workspace / "README.md").read_text() == "readonly\n"
    assert len(modified) == 1
    assert modified[0].name == "train.py"


# ---------------------------------------------------------------------------
# Q8 — Two-flag opt-in for SubscriptionCLIBackend
# ---------------------------------------------------------------------------


def _make_test_workspace(tmp_path):
    """Build a minimal workspace + policy + project_dir for backend construction."""
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    (tmp_path / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(
        workspace=tmp_path, editable=["train.py"],
    )
    return tmp_path, policy


def test_backend_refuses_without_allow_cli_subscription(tmp_path):
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
        SubscriptionCLIBackendError,
    )

    workspace, policy = _make_test_workspace(tmp_path)
    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=False,
        acknowledge_unsandboxed_cli=True,
    )
    with pytest.raises(SubscriptionCLIBackendError, match="allow_cli_subscription"):
        SubscriptionCLIBackend(
            cli_config=cfg, experimental=exp,
            policy=policy, workspace=workspace, project_dir=workspace,
        )


def test_backend_refuses_without_acknowledge_unsandboxed(tmp_path):
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
        SubscriptionCLIBackendError,
    )

    workspace, policy = _make_test_workspace(tmp_path)
    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=False,
    )
    with pytest.raises(SubscriptionCLIBackendError, match="acknowledge_unsandboxed_cli"):
        SubscriptionCLIBackend(
            cli_config=cfg, experimental=exp,
            policy=policy, workspace=workspace, project_dir=workspace,
        )


def test_backend_refuses_when_no_compliance_report(tmp_path, monkeypatch):
    """Reviewer Q2: gate is enforced. Without recent passing report, refuses."""
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
        SubscriptionCLIBackendError,
    )

    # Stub binary resolution so test doesn't need real claude installed
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")

    workspace, policy = _make_test_workspace(tmp_path)
    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=True,
        allow_stale_compliance=False,
    )
    with pytest.raises(SubscriptionCLIBackendError, match="compliance report"):
        SubscriptionCLIBackend(
            cli_config=cfg, experimental=exp,
            policy=policy, workspace=workspace, project_dir=workspace,
        )


def test_backend_starts_with_passing_compliance_report(tmp_path, monkeypatch, caplog):
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
    )

    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")

    workspace, policy = _make_test_workspace(tmp_path)

    # Pre-write a passing compliance report into project_dir/compliance-reports/
    now = datetime.now(timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/fake/claude", "1.0.0", 100, 100,
        now.isoformat().replace("+00:00", "Z"),
    )
    persist_report(rpt, dest_dir=reports_dir_for(workspace))

    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=True,
    )
    backend = SubscriptionCLIBackend(
        cli_config=cfg, experimental=exp,
        policy=policy, workspace=workspace, project_dir=workspace,
    )
    assert backend.backend_kind == "cli_subscription"
    assert backend.capabilities() == {"agent_loop_external", "host_fs_visible"}


def test_backend_starts_with_allow_stale_compliance_flag(tmp_path, monkeypatch, caplog):
    """`allow_stale_compliance=True` lets construction proceed even
    without a recent passing report — but with red-letter warning."""
    import logging
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
    )

    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")

    workspace, policy = _make_test_workspace(tmp_path)
    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=True,
        allow_stale_compliance=True,
    )
    with caplog.at_level(logging.WARNING):
        backend = SubscriptionCLIBackend(
            cli_config=cfg, experimental=exp,
            policy=policy, workspace=workspace, project_dir=workspace,
        )
    # Must have logged the red-letter warning
    assert any("RED-LETTER" in m for m in caplog.messages) or any(
        "stale" in m.lower() for m in caplog.messages
    )


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


def test_factory_dispatches_to_cli_subscription_backend(tmp_path, monkeypatch):
    from crucible.agents import create_agent
    from crucible.config import (
        AgentConfig,
        CLISubscriptionConfig,
        ExperimentalConfig,
    )
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
    )

    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_resolve_binary", lambda self, p: Path("/fake/claude"))
    monkeypatch.setattr(ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0")

    workspace, policy = _make_test_workspace(tmp_path)

    # Pre-seed compliance
    now = datetime.now(timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/fake/claude", "1.0.0", 100, 100,
        now.isoformat().replace("+00:00", "Z"),
    )
    persist_report(rpt, dest_dir=reports_dir_for(workspace))

    config = AgentConfig(
        type="cli-subscription",
        cli_subscription=CLISubscriptionConfig(adapter="claude-code-cli"),
        experimental=ExperimentalConfig(
            allow_cli_subscription=True,
            acknowledge_unsandboxed_cli=True,
        ),
    )
    agent = create_agent(
        config,
        workspace=workspace,
        policy=policy,
        project_dir=workspace,
    )
    assert isinstance(agent, SubscriptionCLIBackend)


def test_factory_unknown_adapter_rejected_at_config_time():
    """Unknown adapter name fails at config validation (before factory)."""
    from crucible.config import ConfigError, _build_cli_subscription
    with pytest.raises(ConfigError, match="must be one of"):
        _build_cli_subscription({"adapter": "ant-colony"})


# ---------------------------------------------------------------------------
# Spec conformance: §3.2 vs §3.3 are different code paths
# ---------------------------------------------------------------------------


def test_compliance_classifications_match_spec_labels():
    """Spec §3.2: parse_failure | model_refusal | format_drift | cli_error."""
    labels = {c.value for c in TrialClassification}
    # All 4 spec-mandated failure labels present:
    assert "parse_failure" in labels
    assert "model_refusal" in labels
    assert "format_drift" in labels
    assert "cli_error" in labels
    # Plus our success bucket:
    assert "parse_success" in labels


def test_safety_filter_is_separate_dimension_from_compliance():
    """Reviewer spec-conformance #1: §3.2 compliance and §3.3 safety
    filter are different dimensions. Ensure the SafetyFilterState enum
    doesn't overlap with TrialClassification labels."""
    safety_values = {s.value for s in SafetyFilterState}
    compliance_values = {c.value for c in TrialClassification}
    assert safety_values.isdisjoint(compliance_values)


def test_compliance_report_path_metadata_is_actual_file_path(tmp_path, monkeypatch):
    """Reviewer round 2 Bug #2 regression: AttemptNode's
    `compliance_report_path` metadata MUST be the path to the JSONL
    report file, not the CLI binary path. Auditors follow this trail
    to find the gate evidence."""
    from crucible.config import CLISubscriptionConfig, ExperimentalConfig
    from crucible.agents.cli_subscription.claude_code_cli import (
        ClaudeCodeCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
    )

    monkeypatch.setattr(
        ClaudeCodeCLIAdapter, "_resolve_binary",
        lambda self, p: Path("/fake/claude"),
    )
    monkeypatch.setattr(
        ClaudeCodeCLIAdapter, "_read_version", lambda self: "1.0.0",
    )

    workspace, policy = _make_test_workspace(tmp_path)
    now = datetime.now(timezone.utc)
    rpt = _make_report(
        "claude-code-cli", "/fake/claude", "1.0.0", 100, 100,
        now.isoformat().replace("+00:00", "Z"),
    )
    report_path = persist_report(rpt, dest_dir=reports_dir_for(workspace))

    cfg = CLISubscriptionConfig(adapter="claude-code-cli")
    exp = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=True,
    )
    backend = SubscriptionCLIBackend(
        cli_config=cfg, experimental=exp,
        policy=policy, workspace=workspace, project_dir=workspace,
    )

    # The backend stored the path of the actual JSONL report file,
    # NOT the CLI binary path.
    assert backend._compliance_report_path == report_path
    assert str(backend._compliance_report_path).endswith(".jsonl"), (
        f"compliance_report_path should be the .jsonl report file, "
        f"got {backend._compliance_report_path!r}"
    )
    # Critically: it's NOT the CLI binary path
    assert str(backend._compliance_report_path) != "/fake/claude"
