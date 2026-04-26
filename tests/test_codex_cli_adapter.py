"""Tests for CodexCLIAdapter — M3 PR 16a.

Reviewer round-1 binding requirements coverage:
  R1#1 sandbox mode    → test_codex_argv_uses_workspace_write_sandbox
  R1#2 spike fixture   → test_codex_parses_spike_fixture (uses real captured
                          JSONL from tests/fixtures/codex_exec_quota_exceeded.jsonl)
  R1#3 §INV-3 forbidden → test_codex_argv_excludes_forbidden_flags
  R1#4 typed auth error → test_codex_parser_raises_typed_auth_error
                          + test_backend_classifies_codex_auth_error_as_auth
  R1#5 phantom-cmd-free → test_codex_auth_error_message_references_real_command
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
)
from crucible.agents.cli_subscription.codex_cli import (
    KNOWN_EVENT_TYPES,
    KNOWN_ITEM_TYPES,
    _AUTH_FAILURE_PHRASES,
    _FORBIDDEN_FLAGS,
    CodexCLIAdapter,
    CodexCLIAuthError,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_adapter(monkeypatch):
    monkeypatch.setattr(
        CodexCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/codex"),
    )
    monkeypatch.setattr(
        CodexCLIAdapter, "_read_version", lambda self: "0.1.0",
    )
    return CodexCLIAdapter()


def _ctx(prompt: str = "do the thing", scratch: str = "/tmp/sc") -> AdapterRunContext:
    return AdapterRunContext(
        prompt=prompt,
        scratch_dir=Path(scratch),
        workspace_root=Path(scratch),
        timeout_seconds=60,
        stdout_cap_bytes=1_000_000,
    )


def _raw(stdout: str, exit_code: int = 0) -> AdapterRawResult:
    return AdapterRawResult(
        argv_redacted=["codex", "exec", "--json"],
        stdout=stdout,
        stderr_tail="",
        exit_code=exit_code,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.5,
    )


# ---------------------------------------------------------------------------
# R1#1 — sandbox mode (workspace-write, NOT read-only)
# ---------------------------------------------------------------------------


def test_codex_argv_uses_workspace_write_sandbox(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx()))
    # `--sandbox workspace-write` must appear as adjacent tokens
    assert "--sandbox" in argv
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "workspace-write", (
        f"Reviewer round 1 #1: must use workspace-write so codex can "
        f"write the edited file. read-only breaks the edit/evaluate loop."
    )


def test_codex_argv_uses_correct_cd(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx(scratch="/path/to/scratch-xyz")))
    assert "--cd" in argv
    cd_idx = argv.index("--cd")
    assert argv[cd_idx + 1] == "/path/to/scratch-xyz"


def test_codex_argv_uses_exec_json_mode(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx()))
    assert argv[1] == "exec"
    assert "--json" in argv


def test_codex_argv_skips_git_repo_check(fake_adapter):
    """Scratch is not a git repo; codex must not refuse to run."""
    argv = list(fake_adapter.build_argv(_ctx()))
    assert "--skip-git-repo-check" in argv


def test_codex_argv_passes_prompt_as_positional(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx(prompt="HELLO PROMPT")))
    assert argv[-1] == "HELLO PROMPT"


# ---------------------------------------------------------------------------
# R1#3 — §INV-3 belt-and-braces: forbidden flags are absent
# ---------------------------------------------------------------------------


def test_codex_argv_excludes_forbidden_flags(fake_adapter):
    """Spec §INV-3: build_argv MUST NOT emit any flag from _FORBIDDEN_FLAGS."""
    argv = list(fake_adapter.build_argv(_ctx()))
    for flag in _FORBIDDEN_FLAGS:
        assert flag not in argv, (
            f"Spec §INV-3 belt-and-braces violation: codex argv "
            f"contains forbidden flag {flag!r}. If a future codex "
            f"release requires this flag, REMOVE it from "
            f"_FORBIDDEN_FLAGS first and document the trade-off."
        )


def test_forbidden_flags_set_includes_codeact_style_flags():
    """Sanity: the forbidden set covers known dangerous shapes."""
    must_be_forbidden = {
        "--code-act", "--repl", "--eval", "--shell", "--bash",
        "--full-auto", "--bypass-approvals",
        "--dangerously-skip-permissions",
    }
    assert must_be_forbidden.issubset(_FORBIDDEN_FLAGS)


# ---------------------------------------------------------------------------
# R1#2 — Parser tested against real spike fixture
# ---------------------------------------------------------------------------


def test_codex_parses_spike_fixture_quota_event(fake_adapter):
    """Parses the real captured JSONL fixture from PR 16a spike.

    The fixture contains the codex stdin-notice prelude + valid event
    types (thread.started/turn.started/error/turn.failed). All event
    types are recognised; quota error message is NOT in
    _AUTH_FAILURE_PHRASES so this is NOT an auth failure (it's a
    quota failure — different classification).
    """
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "codex_exec_quota_exceeded.jsonl"
    )
    stdout = fixture.read_text()

    parsed = fake_adapter.parse_output(_raw(stdout, exit_code=1))

    # All event types in the fixture are known
    event_types = {e.get("type") for e in parsed.structured_events}
    assert event_types <= KNOWN_EVENT_TYPES, (
        f"Spike fixture contained unknown event type(s): "
        f"{event_types - KNOWN_EVENT_TYPES}"
    )
    # Quota-exceeded is not auth — must NOT have raised CodexCLIAuthError
    assert parsed.unknown_schema is False
    # No agent_message item, so description is empty
    assert parsed.description == ""


def test_codex_parses_agent_message_text():
    """item.completed/agent_message contributes to description."""
    stdout = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "x"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({
            "type": "item.completed",
            "item": {"item_type": "agent_message", "text": "Hello world."},
        }),
        json.dumps({"type": "turn.completed"}),
    ])
    with patch.object(CodexCLIAdapter, "_resolve_binary", return_value=Path("/fake/codex")), \
         patch.object(CodexCLIAdapter, "_read_version", return_value="0.1.0"):
        adapter = CodexCLIAdapter()
        parsed = adapter.parse_output(_raw(stdout))

    assert parsed.description == "Hello world."
    assert parsed.unknown_schema is False


def test_codex_marks_unknown_event_type_as_schema_drift(fake_adapter):
    """Unrecognised event type → unknown_schema=True (parse_failure in compliance harness)."""
    stdout = json.dumps({"type": "future.event.type.we.dont.know"})
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.unknown_schema is True


def test_codex_marks_unknown_item_type_as_schema_drift(fake_adapter):
    stdout = json.dumps({
        "type": "item.completed",
        "item": {"item_type": "future_tool_kind"},
    })
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.unknown_schema is True


def test_codex_tolerates_stdin_notice_prelude(fake_adapter):
    """Codex prints `Reading additional input from stdin...` then JSONL.
    The prelude line should NOT mark schema drift."""
    stdout = "Reading additional input from stdin...\n" + json.dumps({
        "type": "thread.started", "thread_id": "x",
    })
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.unknown_schema is False


def test_codex_random_non_json_marks_schema_drift(fake_adapter):
    """Non-JSON line that is not the known prelude → schema drift."""
    stdout = "weird stderr noise\n"
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.unknown_schema is True


def test_codex_detects_command_execution_as_tool_call(fake_adapter):
    stdout = json.dumps({
        "type": "item.completed",
        "item": {"item_type": "command_execution"},
    })
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.tool_was_called is True


# ---------------------------------------------------------------------------
# R1#4 — typed auth-error pattern
# ---------------------------------------------------------------------------


def test_codex_parser_raises_typed_auth_error(fake_adapter):
    """Auth-failure phrase → CodexCLIAuthError (typed exception)."""
    stdout = json.dumps({
        "type": "error",
        "message": "Not authenticated. Run `codex login` to sign in.",
    })
    with pytest.raises(CodexCLIAuthError) as excinfo:
        fake_adapter.parse_output(_raw(stdout, exit_code=1))
    # Evidence captured for forensics
    assert "Not authenticated" in excinfo.value.evidence


def test_codex_auth_error_via_turn_failed_event(fake_adapter):
    """`turn.failed` carries the message under .error.message."""
    stdout = json.dumps({
        "type": "turn.failed",
        "error": {"message": "OAuth credentials expired"},
    })
    with pytest.raises(CodexCLIAuthError):
        fake_adapter.parse_output(_raw(stdout, exit_code=1))


def test_codex_quota_error_is_NOT_classified_as_auth(fake_adapter):
    """Reviewer R1 #4: only declared auth phrases trigger AUTH; the
    fixture's quota-exceeded message must NOT be misclassified."""
    stdout = json.dumps({
        "type": "error",
        "message": "You've hit your usage limit. Upgrade to Pro.",
    })
    # Should NOT raise — quota is not auth
    parsed = fake_adapter.parse_output(_raw(stdout, exit_code=1))
    # Event recorded but no auth raise
    assert any(e.get("type") == "error" for e in parsed.structured_events)


def test_codex_auth_error_phrases_are_explicit_set():
    """The auth-failure detection is by an explicitly DECLARED phrase
    set, not by coincidental substring of generic exception messages
    (PR 19 round 2 lesson). The set is small, exact, and reviewable."""
    # Sanity: declared phrases are real strings
    assert len(_AUTH_FAILURE_PHRASES) >= 4
    for p in _AUTH_FAILURE_PHRASES:
        assert isinstance(p, str) and len(p) > 5


# ---------------------------------------------------------------------------
# R1#5 — phantom-command-free auth error message
# ---------------------------------------------------------------------------


def test_codex_auth_error_message_references_real_command():
    """Reviewer R1 #5 (PR 16 R2 lesson): error message must reference
    real commands / config keys, no phantom flags."""
    err = CodexCLIAuthError("Not authenticated")
    msg = str(err)
    # Real commands / config knobs are mentioned
    assert "codex login" in msg
    assert "agent.cli_subscription.adapter" in msg
    # No invented flags
    assert "--login" not in msg
    assert "--auth" not in msg


# ---------------------------------------------------------------------------
# Backend-side classification: typed exception → AgentErrorType.AUTH
# ---------------------------------------------------------------------------


def test_backend_classifies_codex_auth_error_as_auth(tmp_path, monkeypatch):
    """When CodexCLIAdapter.parse_output raises CodexCLIAuthError,
    SubscriptionCLIBackend must populate
    AgentResult.error_type=AgentErrorType.AUTH.

    Mirrors the ClaudeAgentSDKAuthError → AUTH classification from
    PR 19, except the typed exception originates from the CLI adapter
    rather than the SDK wrapper.

    NB: imports are done inside the test body because test_agents_factory
    evicts crucible.agents.* from sys.modules. Module-level imports at
    the top of this file would point to STALE class objects after that
    eviction, while the backend's lazy import inside generate_edit
    would resolve to FRESH classes — causing the isinstance-based catch
    to fail for module-identity reasons. Fresh in-body import keeps the
    raise-site and catch-site referring to the same class object.
    """
    import sys as _sys
    for _k in list(_sys.modules):
        if _k.startswith("crucible.agents"):
            del _sys.modules[_k]

    from crucible.agents.base import AgentErrorType
    from crucible.agents.cli_subscription.codex_cli import (
        CodexCLIAdapter as FreshCodexCLIAdapter,
    )
    from crucible.agents.cli_subscription_backend import SubscriptionCLIBackend
    from crucible.config import (
        CLISubscriptionConfig,
        ExperimentalConfig,
    )
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(
        workspace=ws, editable=["train.py"]
    )

    # Construct the adapter under monkeypatch (binary path + version
    # snapshot stubbed out), then inject it via SubscriptionCLIBackend.
    # _build_adapter override. Direct class-level monkeypatch is fragile
    # because some tests (test_agents_factory) evict crucible.agents.*
    # from sys.modules, causing a re-import that creates a new class
    # object — so my class-level patch wouldn't apply to the re-imported
    # class used inside _build_adapter.
    monkeypatch.setattr(
        FreshCodexCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/codex"),
    )
    monkeypatch.setattr(
        FreshCodexCLIAdapter, "_read_version", lambda self: "0.1.0",
    )
    auth_event = json.dumps({
        "type": "error",
        "message": "Not authenticated. Run `codex login` to sign in.",
    })
    monkeypatch.setattr(
        FreshCodexCLIAdapter, "run_subprocess",
        lambda self, ctx: _raw(auth_event, exit_code=1),
    )
    prebuilt_adapter = FreshCodexCLIAdapter()

    # Override _build_adapter so the backend uses our prebuilt instance
    # (immune to the sys.modules eviction issue described above).
    monkeypatch.setattr(
        SubscriptionCLIBackend, "_build_adapter",
        lambda self: prebuilt_adapter,
    )

    cli_cfg = CLISubscriptionConfig(
        adapter="codex-cli",
        timeout_seconds=30,
        stdout_cap_bytes=1_000_000,
    )
    exp_cfg = ExperimentalConfig(
        allow_cli_subscription=True,
        acknowledge_unsandboxed_cli=True,
        allow_stale_compliance=True,
    )

    backend = SubscriptionCLIBackend(
        cli_config=cli_cfg,
        experimental=exp_cfg,
        workspace=ws,
        policy=policy,
    )

    result = backend.generate_edit("anything", ws)
    assert result.error_type == AgentErrorType.AUTH, (
        f"Expected AUTH classification via typed CodexCLIAuthError, got "
        f"{result.error_type!r}"
    )
