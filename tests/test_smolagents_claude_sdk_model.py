"""Tests for `crucible.agents.smolagents_claude_sdk_model` — M3 PR 19.

The CRITICAL test in this module is `test_sdk_is_invoked_with_no_internal_tools`
— it locks in the ACL-preservation invariant that a future SDK update
must not silently break.

Other tests cover:
  - Message-format conversion (smolagents → SDK prompt string)
  - Auth-failure classification
  - Stream draining (AssistantMessage / ResultMessage handling)
  - Sync wrapper around async SDK
  - asyncio.run() loud failure inside a running loop

Skipped when smolagents isn't installed (importorskip).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip module if smolagents extras not installed
pytest.importorskip("smolagents")
pytest.importorskip("claude_agent_sdk")

from crucible.agents.smolagents_claude_sdk_model import (
    ClaudeAgentSDKAuthError,
    ClaudeAgentSDKModel,
    _SDK_DISALLOWED_TOOLS,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic SDK event stream
# ---------------------------------------------------------------------------


class _FakeAssistantMessage:
    """Stand-in for `claude_agent_sdk.AssistantMessage`. Has `.content` list of
    `_FakeTextBlock`s. We match by isinstance against the REAL types in the
    code under test, so we monkey-patch those imports at call time."""
    def __init__(self, text: str):
        self.content = [_FakeTextBlock(text)]


class _FakeTextBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResultMessage:
    def __init__(self, total_cost_usd: float | None = None):
        self.total_cost_usd = total_cost_usd


async def _stream_events(events: list[Any]):
    for event in events:
        yield event


# ---------------------------------------------------------------------------
# CRITICAL — ACL invariant
# ---------------------------------------------------------------------------


def test_sdk_is_invoked_with_no_internal_tools(monkeypatch):
    """**Reviewer round 1 critical pin** — locks in the ACL invariant:
    SDK must be invoked with `allowed_tools=[]`, `max_turns=1`, and an
    exhaustive `disallowed_tools` list. Any future drift breaks the
    invariant; this test catches it.

    Without this configuration, the SDK runs a full agent loop with its
    own tools, voiding the smolagents `CheatResistancePolicy` ACL
    boundary (the §3.3 agent-loop-in-agent-loop problem)."""
    from crucible.agents import smolagents_claude_sdk_model as sdk_module

    captured = {}

    async def _fake_query(*, prompt, options, transport=None):
        captured["options"] = options
        captured["prompt"] = prompt
        # Yield nothing — empty stream is fine for this test
        if False:
            yield None

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)
    # Patch the AssistantMessage and friends that the module imports inline
    # so isinstance checks still work
    monkeypatch.setattr(
        sdk_module, "ClaudeAgentOptions",
        # Use the REAL ClaudeAgentOptions for the assertion
        __import__("claude_agent_sdk").ClaudeAgentOptions,
        raising=False,
    )

    model = ClaudeAgentSDKModel(model="claude-3-5-sonnet-20241022")
    asyncio.run(model._async_call("hello"))

    opts = captured["options"]
    # The ACL invariant — these MUST hold
    assert opts.allowed_tools == [], (
        f"allowed_tools must be empty for ACL invariant; got {opts.allowed_tools!r}"
    )
    assert opts.max_turns == 1, (
        f"max_turns must be 1 to prevent SDK agent loop; got {opts.max_turns!r}"
    )
    # disallowed_tools is exhaustive — at least covers the known set
    must_disallow = {"Read", "Edit", "Write", "Glob", "Grep", "Bash"}
    assert must_disallow <= set(opts.disallowed_tools or []), (
        f"disallowed_tools missing required entries; got {opts.disallowed_tools!r}"
    )
    # can_use_tool callback wired in
    assert opts.can_use_tool is not None, "can_use_tool callback must be set"


def test_can_use_tool_callback_denies_everything():
    """The `can_use_tool` callback (defense-in-depth) must return a
    `{"behavior": "deny"}` shape for any tool call."""
    from crucible.agents.smolagents_claude_sdk_model import _deny_all_tools

    result = asyncio.run(_deny_all_tools("Read", {"path": "/etc/passwd"}, {}))
    assert result["behavior"] == "deny"
    assert "deliberately denies" in result["message"]


def test_disallowed_tools_list_has_known_dangerous_set():
    """Static check on the module-level constant — sanity for future readers."""
    must_have = {"Read", "Edit", "Write", "Glob", "Grep", "Bash"}
    assert must_have <= set(_SDK_DISALLOWED_TOOLS), (
        f"_SDK_DISALLOWED_TOOLS missing required entries; got {_SDK_DISALLOWED_TOOLS!r}"
    )


# ---------------------------------------------------------------------------
# Message format conversion
# ---------------------------------------------------------------------------


def test_format_prompt_handles_dict_messages():
    model = ClaudeAgentSDKModel()
    msgs = [
        {"role": "system", "content": "you are a helper"},
        {"role": "user", "content": "do the thing"},
    ]
    out = model._format_prompt(msgs, tools_to_call_from=None)
    assert "system:" in out
    assert "you are a helper" in out
    assert "user:" in out
    assert "do the thing" in out


def test_format_prompt_handles_list_content():
    """smolagents content can be list of {type, text} blocks."""
    model = ClaudeAgentSDKModel()
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    out = model._format_prompt(msgs, tools_to_call_from=None)
    assert "hi" in out


# ---------------------------------------------------------------------------
# Stream draining (AssistantMessage text accumulation)
# ---------------------------------------------------------------------------


def test_stream_draining_accumulates_text(monkeypatch):
    """AssistantMessage text blocks are accumulated; ResultMessage cost
    surfaces in events list."""
    from crucible.agents import smolagents_claude_sdk_model as sdk_module
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    # Patch the real SDK's query() to yield our synthetic events
    async def _fake_query(*, prompt, options, transport=None):
        yield AssistantMessage(
            content=[TextBlock(text="hello "), TextBlock(text="world")],
            model="claude-3-5-sonnet",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id="test-session",
            total_cost_usd=0.001,
        )

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    text, events = asyncio.run(model._async_call("test prompt"))
    assert text == "hello world"
    # ResultMessage event recorded with cost
    assert any(e.get("estimated_cost_usd") == 0.001 for e in events)


# ---------------------------------------------------------------------------
# generate() returns ChatMessage
# ---------------------------------------------------------------------------


def test_generate_returns_chat_message_with_text(monkeypatch):
    from crucible.agents import smolagents_claude_sdk_model as sdk_module
    from claude_agent_sdk import AssistantMessage, TextBlock
    from smolagents.models import MessageRole

    async def _fake_query(*, prompt, options, transport=None):
        yield AssistantMessage(
            content=[TextBlock(text="response text")],
            model="claude-3-5-sonnet",
        )

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    chat_msg = model.generate([{"role": "user", "content": "hi"}])
    assert chat_msg.role == MessageRole.ASSISTANT
    assert chat_msg.content == "response text"


# ---------------------------------------------------------------------------
# Auth-failure classification
# ---------------------------------------------------------------------------


def test_missing_credentials_raises_auth_error(monkeypatch):
    """When `claude_agent_sdk` can't find OAuth creds, the wrapper
    converts to `ClaudeAgentSDKAuthError` with a clear "run claude login"
    message. The orchestrator can map this to AgentErrorType.AUTH."""
    from crucible.agents import smolagents_claude_sdk_model as sdk_module

    async def _fake_query(*, prompt, options, transport=None):
        raise FileNotFoundError("Could not find credentials.json")
        # Never reached:
        if False:
            yield None

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    with pytest.raises(ClaudeAgentSDKAuthError, match="claude login"):
        asyncio.run(model._async_call("hi"))


def test_oserror_classified_as_auth_error(monkeypatch):
    """OSError variants from the SDK (creds unreadable, etc.) also map to AUTH."""
    from crucible.agents import smolagents_claude_sdk_model as sdk_module

    async def _fake_query(*, prompt, options, transport=None):
        raise OSError("Permission denied")
        if False:
            yield None

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    with pytest.raises(ClaudeAgentSDKAuthError):
        asyncio.run(model._async_call("hi"))


# ---------------------------------------------------------------------------
# asyncio.run() loud failure inside a running loop
# ---------------------------------------------------------------------------


def test_generate_loud_failure_inside_running_loop(monkeypatch):
    """If `generate()` is called from inside an existing event loop,
    `asyncio.run()` raises with a documented hint about the future
    async-smolagents path."""
    from crucible.agents import smolagents_claude_sdk_model as sdk_module

    # Simulate the scenario: monkey-patch asyncio.run to raise the
    # exact RuntimeError shape Python emits when called from inside a loop.
    def _fake_run(coro):
        # Close the unused coroutine to suppress Python's warning
        coro.close()
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    monkeypatch.setattr(asyncio, "run", _fake_run)

    model = ClaudeAgentSDKModel()
    with pytest.raises(RuntimeError, match="cannot be called from inside an existing"):
        model.generate([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# SmolagentsBackend integration: provider="claude-subscription"
# ---------------------------------------------------------------------------


def test_smolagents_backend_uses_claude_sdk_model_when_configured(tmp_path, monkeypatch):
    """When `SmolagentsConfig.provider == "claude-subscription"`, the
    backend constructs `ClaudeAgentSDKModel` instead of `LiteLLMModel`."""
    from pathlib import Path
    from crucible.config import SmolagentsConfig
    from crucible.agents.smolagents_backend import SmolagentsBackend
    from crucible.agents.smolagents_claude_sdk_model import ClaudeAgentSDKModel
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    # Build minimal fixture
    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(workspace=ws, editable=["train.py"])
    config = SmolagentsConfig(
        provider="claude-subscription",
        model="claude-3-5-sonnet-20241022",
    )

    backend = SmolagentsBackend(
        config=config,
        policy=policy,
        workspace=Path(ws),
    )
    assert isinstance(backend._model, ClaudeAgentSDKModel)


def test_smolagents_backend_uses_litellm_for_other_providers(tmp_path, monkeypatch):
    """Backward-compat: existing `provider="anthropic"` (or any other
    LiteLLM-routed value) still uses LiteLLMModel."""
    from pathlib import Path
    from crucible.config import SmolagentsConfig
    from crucible.agents.smolagents_backend import SmolagentsBackend
    from smolagents import LiteLLMModel
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # avoid the warning
    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(workspace=ws, editable=["train.py"])
    config = SmolagentsConfig(provider="anthropic")

    backend = SmolagentsBackend(
        config=config,
        policy=policy,
        workspace=Path(ws),
    )
    assert isinstance(backend._model, LiteLLMModel)
