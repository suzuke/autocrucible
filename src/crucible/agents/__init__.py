"""Agent factory for creating agent instances from config."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from crucible.agents.base import AgentInterface, AgentResult
from crucible.config import AgentConfig

if TYPE_CHECKING:
    from crucible.security.cheat_resistance_policy import CheatResistancePolicy

__all__ = ["AgentInterface", "AgentResult", "create_agent"]


def create_agent(config: AgentConfig, **kwargs) -> AgentInterface:
    """Create an agent instance based on `config.type`.

    Supported types:
      - "claude-code"  → ClaudeCodeAgent (default; existing behavior)
      - "smolagents"   → SmolagentsBackend (M2 PR 13; requires
                          `pip install autocrucible[smolagents]`)

    For smolagents, callers MUST pass `workspace: Path` and
    `policy: CheatResistancePolicy` kwargs — they're required to
    construct the ACL-enforced tools.

    Reviewer round 1 pin: factory construction is pure (no network
    calls). Smolagents import is lazy — only triggered when type is
    actually `"smolagents"`. Loading config alone never imports
    smolagents.
    """
    if config.type == "claude-code":
        return _create_claude_code(config, **kwargs)
    if config.type == "smolagents":
        return _create_smolagents(config, **kwargs)
    # Defensive: should be caught by config validator (`_build_agent`),
    # but fail closed if anyone bypasses the config layer.
    raise ValueError(f"unknown agent type: {config.type!r}")


def _create_claude_code(config: AgentConfig, **kwargs) -> AgentInterface:
    """Construct the existing claude_agent_sdk-based backend."""
    from crucible.agents.claude_code import ClaudeCodeAgent

    defaults: dict[str, object] = {}
    if config.model is not None:
        defaults["model"] = config.model
    if config.language is not None:
        defaults["language"] = config.language
    # Smolagents-only kwargs are dropped silently for claude-code.
    merged = {
        **defaults,
        **{
            k: v for k, v in kwargs.items()
            if k not in ("policy", "workspace")
        },
    }
    return ClaudeCodeAgent(**merged)


def _create_smolagents(config: AgentConfig, **kwargs) -> AgentInterface:
    """Construct the smolagents-based backend (lazy import)."""
    workspace = kwargs.get("workspace")
    policy = kwargs.get("policy")
    if workspace is None or policy is None:
        raise ValueError(
            "smolagents AgentBackend requires `workspace: Path` and "
            "`policy: CheatResistancePolicy` kwargs. The CLI's `run` "
            "command passes these automatically; programmatic callers "
            "must supply them."
        )

    from crucible.agents.smolagents_backend import (
        SmolagentsBackend,
        SmolagentsImportError,
    )

    # Re-raise SmolagentsImportError as-is so the CLI / caller can
    # surface its install instructions verbatim.
    return SmolagentsBackend(
        config=config.smolagents,
        policy=policy,
        workspace=Path(workspace),
        system_prompt=config.system_prompt,
    )
