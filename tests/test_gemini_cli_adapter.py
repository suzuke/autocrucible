"""Tests for GeminiCLIAdapter — M3 PR 16b.

Mirrors the test structure from PR 16a's `test_codex_cli_adapter.py`,
adapted for gemini's stream-json schema. Coverage:

  - Argv shape: -p prompt, -o stream-json, --approval-mode default,
    --skip-trust
  - §INV-3 forbidden flags: `--yolo`, `-y`, `--accept-raw-output-risk`,
    `--raw-output` absent; forbidden approval modes (yolo, auto_edit)
    not passed via --approval-mode
  - Parser: spike fixture round-trip (gemini_stream_json_tool_call.jsonl),
    assistant message aggregation, unknown event type → schema drift,
    tool_use → tool_was_called
  - Typed auth: phrase match → GeminiCLIAuthError, stderr fallback,
    explicit-set sanity, phantom-command-free message
  - Backend integration: parse_output raises CLISubscriptionAuthError
    base → AgentResult.error_type=AUTH (sys.modules-eviction-tolerant
    via in-body imports — see PR 16a test docstring for rationale)
  - CLISubscriptionAuthError base class hierarchy: GeminiCLIAuthError
    is a subclass; CodexCLIAuthError is also a subclass (PR 16b
    refactor in same diff).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
    CLISubscriptionAuthError,
)
from crucible.agents.cli_subscription.gemini_cli import (
    KNOWN_EVENT_TYPES,
    _AUTH_FAILURE_PHRASES,
    _FORBIDDEN_APPROVAL_MODES,
    _FORBIDDEN_FLAGS,
    GeminiCLIAdapter,
    GeminiCLIAuthError,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_adapter(monkeypatch):
    monkeypatch.setattr(
        GeminiCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/gemini"),
    )
    monkeypatch.setattr(
        GeminiCLIAdapter, "_read_version", lambda self: "0.39.1",
    )
    return GeminiCLIAdapter()


def _ctx(prompt: str = "do the thing", scratch: str = "/tmp/sc") -> AdapterRunContext:
    return AdapterRunContext(
        prompt=prompt,
        scratch_dir=Path(scratch),
        workspace_root=Path(scratch),
        timeout_seconds=60,
        stdout_cap_bytes=1_000_000,
    )


def _raw(stdout: str, stderr_tail: str = "", exit_code: int = 0) -> AdapterRawResult:
    return AdapterRawResult(
        argv_redacted=["gemini", "-p", "<x>", "-o", "stream-json"],
        stdout=stdout,
        stderr_tail=stderr_tail,
        exit_code=exit_code,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.5,
    )


# ---------------------------------------------------------------------------
# Argv shape
# ---------------------------------------------------------------------------


def test_gemini_argv_uses_headless_mode(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx(prompt="HELLO")))
    assert "-p" in argv
    p_idx = argv.index("-p")
    assert argv[p_idx + 1] == "HELLO"


def test_gemini_argv_uses_stream_json(fake_adapter):
    argv = list(fake_adapter.build_argv(_ctx()))
    assert "-o" in argv
    assert "stream-json" in argv


def test_gemini_argv_uses_default_approval_mode(fake_adapter):
    """The `default` mode prompts for approval — opposite of `yolo`/`auto_edit`."""
    argv = list(fake_adapter.build_argv(_ctx()))
    assert "--approval-mode" in argv
    am_idx = argv.index("--approval-mode")
    assert argv[am_idx + 1] == "default", (
        f"approval-mode must be 'default'; never `yolo` or `auto_edit`"
    )


def test_gemini_argv_includes_skip_trust(fake_adapter):
    """Without --skip-trust, gemini blocks on stdin asking the user to
    trust the workspace — fatal in headless mode."""
    argv = list(fake_adapter.build_argv(_ctx()))
    assert "--skip-trust" in argv


# ---------------------------------------------------------------------------
# §INV-3 belt-and-braces
# ---------------------------------------------------------------------------


def test_gemini_argv_excludes_forbidden_flags(fake_adapter):
    """Spec §INV-3: build_argv MUST NOT emit any flag from _FORBIDDEN_FLAGS."""
    argv = list(fake_adapter.build_argv(_ctx()))
    for flag in _FORBIDDEN_FLAGS:
        assert flag not in argv, (
            f"Spec §INV-3 belt-and-braces violation: gemini argv "
            f"contains forbidden flag {flag!r}"
        )


def test_gemini_argv_does_not_pass_forbidden_approval_modes(fake_adapter):
    """`--approval-mode` may appear, but its VALUE must not be in
    _FORBIDDEN_APPROVAL_MODES (yolo, auto_edit)."""
    argv = list(fake_adapter.build_argv(_ctx()))
    if "--approval-mode" in argv:
        idx = argv.index("--approval-mode")
        value = argv[idx + 1]
        assert value not in _FORBIDDEN_APPROVAL_MODES, (
            f"approval-mode={value!r} auto-approves tool calls (§INV-3 violation)"
        )


def test_forbidden_flags_set_includes_yolo_shapes():
    """Sanity: the forbidden set covers gemini's known auto-approval flags."""
    must_be_forbidden = {"--yolo", "-y"}
    assert must_be_forbidden.issubset(_FORBIDDEN_FLAGS)


def test_forbidden_approval_modes_includes_yolo_and_auto_edit():
    assert "yolo" in _FORBIDDEN_APPROVAL_MODES
    assert "auto_edit" in _FORBIDDEN_APPROVAL_MODES


# ---------------------------------------------------------------------------
# Parser — spike fixture
# ---------------------------------------------------------------------------


def test_gemini_parses_spike_fixture(fake_adapter):
    """Parses the real captured JSONL fixture from PR 16b spike.

    The fixture covers init / message (user) / tool_use / tool_result /
    message (assistant, two delta chunks) / result. All event types in
    KNOWN_EVENT_TYPES; tool_was_called is set; description aggregates
    the assistant message chunks.
    """
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "gemini_stream_json_tool_call.jsonl"
    )
    stdout = fixture.read_text()
    parsed = fake_adapter.parse_output(_raw(stdout))

    event_types = {e.get("type") for e in parsed.structured_events}
    assert event_types <= KNOWN_EVENT_TYPES, (
        f"Spike fixture contained unknown event type(s): "
        f"{event_types - KNOWN_EVENT_TYPES}"
    )
    assert parsed.unknown_schema is False
    assert parsed.tool_was_called is True
    # Description aggregates the two assistant delta chunks
    assert "test.py" in parsed.description
    assert "x = 1" in parsed.description


def test_gemini_aggregates_assistant_messages():
    """Multiple assistant message events combine into description."""
    stdout = "\n".join([
        json.dumps({"type": "init", "session_id": "s", "model": "gemini-3"}),
        json.dumps({"type": "message", "role": "user", "content": "Hi"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Hello "}),
        json.dumps({"type": "message", "role": "assistant", "content": "world."}),
        json.dumps({"type": "result", "status": "success", "stats": {}}),
    ])
    with patch.object(GeminiCLIAdapter, "_resolve_binary", return_value=Path("/fake/gemini")), \
         patch.object(GeminiCLIAdapter, "_read_version", return_value="0.39.1"):
        adapter = GeminiCLIAdapter()
        parsed = adapter.parse_output(_raw(stdout))

    assert parsed.description == "Hello world."
    assert parsed.unknown_schema is False


def test_gemini_user_messages_excluded_from_description(fake_adapter):
    """Only assistant messages contribute to description, not user echo."""
    stdout = "\n".join([
        json.dumps({"type": "message", "role": "user", "content": "MY PROMPT"}),
        json.dumps({"type": "message", "role": "assistant", "content": "the response"}),
    ])
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert "MY PROMPT" not in parsed.description
    assert parsed.description == "the response"


def test_gemini_marks_unknown_event_type_as_schema_drift(fake_adapter):
    stdout = json.dumps({"type": "future.event.kind"})
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.unknown_schema is True


def test_gemini_tool_use_marks_tool_was_called(fake_adapter):
    stdout = json.dumps({
        "type": "tool_use",
        "tool_name": "read_file",
        "tool_id": "x",
        "parameters": {},
    })
    parsed = fake_adapter.parse_output(_raw(stdout))
    assert parsed.tool_was_called is True


def test_gemini_random_non_json_marks_schema_drift(fake_adapter):
    parsed = fake_adapter.parse_output(_raw("garbage line\n"))
    assert parsed.unknown_schema is True


# ---------------------------------------------------------------------------
# Typed auth-error
# ---------------------------------------------------------------------------


def test_gemini_parser_raises_typed_auth_error_on_result_event(fake_adapter):
    """Auth-failure phrase in a non-success `result` event → GeminiCLIAuthError."""
    stdout = json.dumps({
        "type": "result",
        "status": "error",
        "message": "Authentication required. Run `gemini auth` to sign in.",
    })
    with pytest.raises(GeminiCLIAuthError) as excinfo:
        fake_adapter.parse_output(_raw(stdout, exit_code=1))
    assert "Authentication required" in excinfo.value.evidence


def test_gemini_parser_raises_auth_error_via_assistant_message(fake_adapter):
    """Some auth phrases surface as assistant messages (gemini explaining)."""
    stdout = json.dumps({
        "type": "message",
        "role": "assistant",
        "content": "I'm sorry — Please sign in before requesting code edits.",
    })
    with pytest.raises(GeminiCLIAuthError):
        fake_adapter.parse_output(_raw(stdout))


def test_gemini_auth_error_detected_in_stderr_when_no_jsonl(fake_adapter):
    """Stderr fallback (mirrors codex_cli pattern PR 16a R2 #1)."""
    raw = AdapterRawResult(
        argv_redacted=["gemini"],
        stdout="",
        stderr_tail="GEMINI_API_KEY is not set. Run `gemini auth` or set the env var.",
        exit_code=1,
        timed_out=False,
        stdout_cap_exceeded=False,
        duration_seconds=0.05,
    )
    with pytest.raises(GeminiCLIAuthError) as excinfo:
        fake_adapter.parse_output(raw)
    assert "GEMINI_API_KEY" in excinfo.value.evidence


def test_gemini_quota_or_other_error_NOT_classified_as_auth(fake_adapter):
    """Non-auth error events must NOT raise GeminiCLIAuthError."""
    stdout = json.dumps({
        "type": "result",
        "status": "error",
        "message": "Rate limit exceeded. Try again later.",
    })
    parsed = fake_adapter.parse_output(_raw(stdout, exit_code=1))
    # No raise — error event recorded
    assert any(e.get("type") == "result" for e in parsed.structured_events)


def test_gemini_auth_error_phrases_are_explicit_set():
    assert len(_AUTH_FAILURE_PHRASES) >= 4
    for p in _AUTH_FAILURE_PHRASES:
        assert isinstance(p, str) and len(p) > 5


def test_gemini_auth_error_message_references_real_command():
    err = GeminiCLIAuthError("Not signed in")
    msg = str(err)
    assert "gemini auth" in msg
    assert "GEMINI_API_KEY" in msg
    assert "agent.cli_subscription.adapter" in msg
    # No invented flags
    assert "--login" not in msg


# ---------------------------------------------------------------------------
# Base class hierarchy (PR 16a R2 follow-up #4 — refactor done in PR 16b)
# ---------------------------------------------------------------------------


def test_gemini_auth_error_subclasses_cli_subscription_auth_error():
    """GeminiCLIAuthError must inherit from CLISubscriptionAuthError so
    backend's single isinstance-catch covers all adapters.

    NB: in-body re-import to dodge sys.modules eviction by
    test_agents_factory (see PR 16a's analogous test for rationale).
    """
    import sys as _sys
    for _k in list(_sys.modules):
        if _k.startswith("crucible.agents"):
            del _sys.modules[_k]
    from crucible.agents.cli_subscription.base import (
        CLISubscriptionAuthError as FreshBase,
    )
    from crucible.agents.cli_subscription.gemini_cli import (
        GeminiCLIAuthError as FreshGemini,
    )
    assert issubclass(FreshGemini, FreshBase)


def test_codex_auth_error_also_subclasses_cli_subscription_auth_error():
    """PR 16b's refactor: CodexCLIAuthError now also inherits from
    the common base. Single isinstance-catch in backend handles both."""
    import sys as _sys
    for _k in list(_sys.modules):
        if _k.startswith("crucible.agents"):
            del _sys.modules[_k]
    from crucible.agents.cli_subscription.base import (
        CLISubscriptionAuthError as FreshBase,
    )
    from crucible.agents.cli_subscription.codex_cli import (
        CodexCLIAuthError as FreshCodex,
    )
    assert issubclass(FreshCodex, FreshBase)


# ---------------------------------------------------------------------------
# Backend integration
# ---------------------------------------------------------------------------


def test_backend_classifies_gemini_auth_error_as_auth(tmp_path, monkeypatch):
    """End-to-end: GeminiCLIAdapter raises GeminiCLIAuthError →
    SubscriptionCLIBackend catches via CLISubscriptionAuthError base →
    AgentResult.error_type=AUTH.

    In-body imports + sys.modules-eviction tolerance (see PR 16a's
    analogous test for rationale).
    """
    import sys as _sys
    for _k in list(_sys.modules):
        if _k.startswith("crucible.agents"):
            del _sys.modules[_k]

    from crucible.agents.base import AgentErrorType
    from crucible.agents.cli_subscription.gemini_cli import (
        GeminiCLIAdapter as FreshGeminiCLIAdapter,
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

    monkeypatch.setattr(
        FreshGeminiCLIAdapter, "_resolve_binary",
        lambda self, override: Path("/fake/gemini"),
    )
    monkeypatch.setattr(
        FreshGeminiCLIAdapter, "_read_version", lambda self: "0.39.1",
    )
    auth_event = json.dumps({
        "type": "result",
        "status": "error",
        "message": "Please sign in via `gemini auth`.",
    })
    monkeypatch.setattr(
        FreshGeminiCLIAdapter, "run_subprocess",
        lambda self, ctx: _raw(auth_event, exit_code=1),
    )
    prebuilt_adapter = FreshGeminiCLIAdapter()

    monkeypatch.setattr(
        SubscriptionCLIBackend, "_build_adapter",
        lambda self: prebuilt_adapter,
    )

    cli_cfg = CLISubscriptionConfig(
        adapter="gemini-cli",
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
        f"Expected AUTH classification via typed GeminiCLIAuthError, got "
        f"{result.error_type!r}"
    )
