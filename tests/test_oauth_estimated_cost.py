"""Tests for M3 PR 19a — `usage_source="oauth_estimated"` plumbing.

Closes the loop on PR 19's deferred TODO: surface the API-equivalent
cost estimate from `claude_agent_sdk.ResultMessage` onto the
AttemptNode so postmortem can render it with the correct
disambiguation per spec §4.1 (`oauth_estimated` ≠ `api`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import get_args
from unittest.mock import MagicMock

import pytest

pytest.importorskip("smolagents")
pytest.importorskip("claude_agent_sdk")

from crucible.agents.smolagents_claude_sdk_model import ClaudeAgentSDKModel
from crucible.ledger import UsageSource


# ---------------------------------------------------------------------------
# Schema additions
# ---------------------------------------------------------------------------


def test_oauth_estimated_in_usage_source_literal():
    """Spec §4.1 enum extension — `oauth_estimated` must be a valid UsageSource."""
    values = set(get_args(UsageSource))
    assert "oauth_estimated" in values
    # Existing values still present for backward compat
    assert "api" in values
    assert "cli_estimated" in values
    assert "unavailable" in values


# ---------------------------------------------------------------------------
# Cost accumulation in ClaudeAgentSDKModel
# ---------------------------------------------------------------------------


def test_cumulative_cost_starts_at_zero():
    model = ClaudeAgentSDKModel()
    assert model.cumulative_cost_estimate_usd == 0.0


def test_reset_cumulative_cost():
    model = ClaudeAgentSDKModel()
    model._cumulative_cost_estimate_usd = 1.23
    model.reset_cumulative_cost()
    assert model.cumulative_cost_estimate_usd == 0.0


def test_cost_accumulates_across_generate_calls(monkeypatch):
    """Each `ResultMessage.total_cost_usd` adds to the cumulative estimate."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    call_count = {"n": 0}

    async def _fake_query(*, prompt, options, transport=None):
        call_count["n"] += 1
        yield AssistantMessage(
            content=[TextBlock(text=f"response {call_count['n']}")],
            model="claude-3-5-sonnet",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=80,
            is_error=False,
            num_turns=1,
            session_id=f"session-{call_count['n']}",
            total_cost_usd=0.001 * call_count["n"],
        )

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    model.generate([{"role": "user", "content": "first"}])
    model.generate([{"role": "user", "content": "second"}])
    model.generate([{"role": "user", "content": "third"}])

    # 0.001 + 0.002 + 0.003 = 0.006
    assert abs(model.cumulative_cost_estimate_usd - 0.006) < 1e-9


def test_reset_clears_for_new_attempt(monkeypatch):
    """SmolagentsBackend resets between attempts so cost is per-attempt."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    async def _fake_query(*, prompt, options, transport=None):
        yield AssistantMessage(content=[TextBlock(text="ok")], model="x")
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.005,
        )

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    model = ClaudeAgentSDKModel()
    model.generate([{"role": "user", "content": "x"}])
    assert model.cumulative_cost_estimate_usd == 0.005

    model.reset_cumulative_cost()
    model.generate([{"role": "user", "content": "y"}])
    # Only the second attempt's cost
    assert model.cumulative_cost_estimate_usd == 0.005


# ---------------------------------------------------------------------------
# SmolagentsBackend surfaces cost via backend_metadata
# ---------------------------------------------------------------------------


def test_backend_metadata_includes_oauth_estimated_cost(tmp_path, monkeypatch):
    """When provider="claude-subscription" and the model accumulated
    cost, AgentResult.backend_metadata should include
    cost_usd + usage_source="oauth_estimated"."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
    from crucible.config import SmolagentsConfig
    from crucible.agents.smolagents_backend import SmolagentsBackend
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    async def _fake_query(*, prompt, options, transport=None):
        yield AssistantMessage(content=[TextBlock(text="done")], model="x")
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", total_cost_usd=0.0123,
        )

    monkeypatch.setattr("claude_agent_sdk.query", _fake_query, raising=True)

    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(workspace=ws, editable=["train.py"])
    config = SmolagentsConfig(provider="claude-subscription")
    backend = SmolagentsBackend(
        config=config, policy=policy, workspace=Path(ws),
    )

    # Stub agent.run to invoke the model once so cost accumulates
    def _fake_run(prompt, max_steps=12, **kwargs):
        # smolagents ToolCallingAgent normally calls model many times;
        # we only need ONE call to verify cost surfacing.
        backend._model.generate([{"role": "user", "content": prompt}])
        return "ok"

    backend._agent.run = _fake_run
    result = backend.generate_edit("anything", Path(ws))

    assert result.backend_metadata.get("usage_source") == "oauth_estimated"
    assert result.backend_metadata.get("cost_usd") == pytest.approx(0.0123)


def test_backend_metadata_omits_cost_when_zero(tmp_path, monkeypatch):
    """If model didn't accumulate any cost (e.g. agent.run failed before
    first generate()), don't pollute metadata with cost_usd=0."""
    from crucible.config import SmolagentsConfig
    from crucible.agents.smolagents_backend import SmolagentsBackend
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(workspace=ws, editable=["train.py"])
    config = SmolagentsConfig(provider="claude-subscription")
    backend = SmolagentsBackend(
        config=config, policy=policy, workspace=Path(ws),
    )

    # agent.run that never invokes the model
    backend._agent.run = lambda prompt, **kwargs: "no cost"
    result = backend.generate_edit("anything", Path(ws))

    assert "cost_usd" not in result.backend_metadata
    assert "usage_source" not in result.backend_metadata


def test_litellm_path_does_not_set_oauth_metadata(tmp_path, monkeypatch):
    """LiteLLMModel doesn't have `cumulative_cost_estimate_usd`. Backend
    must NOT mistakenly populate oauth_estimated for non-CC paths."""
    from crucible.config import SmolagentsConfig
    from crucible.agents.smolagents_backend import SmolagentsBackend
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    ws = tmp_path
    (ws / "train.py").write_text("x = 1\n")
    policy = CheatResistancePolicy.from_lists(workspace=ws, editable=["train.py"])
    config = SmolagentsConfig(provider="anthropic")  # LiteLLM path
    backend = SmolagentsBackend(
        config=config, policy=policy, workspace=Path(ws),
    )

    backend._agent.run = lambda prompt, **kwargs: "ok"
    result = backend.generate_edit("anything", Path(ws))

    # No cost_usd from oauth path; the existing record.usage path
    # (separate from this PR) handles "api" classification elsewhere
    assert "cost_usd" not in result.backend_metadata
    assert result.backend_metadata.get("usage_source") != "oauth_estimated"
