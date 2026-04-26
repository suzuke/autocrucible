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
      - "claude-code"      → ClaudeCodeAgent (default; SDK-based)
      - "smolagents"       → SmolagentsBackend (M2 PR 13)
      - "cli-subscription" → SubscriptionCLIBackend (M3 PR 16,
                              EXPERIMENTAL — see two-flag opt-in)

    For smolagents and cli-subscription, callers MUST pass
    `workspace: Path` and `policy: CheatResistancePolicy` kwargs —
    they're required to construct the ACL-enforced tools / scratch
    dir setup.

    Factory construction is pure (no network calls). Backend imports
    are lazy — only triggered when the matching `config.type` is
    requested. Loading config alone never imports smolagents or any
    CLI subprocess module.
    """
    if config.type == "claude-code":
        return _create_claude_code(config, **kwargs)
    if config.type == "smolagents":
        return _create_smolagents(config, **kwargs)
    if config.type == "cli-subscription":
        return _create_cli_subscription(config, **kwargs)
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


def _create_cli_subscription(config: AgentConfig, **kwargs) -> AgentInterface:
    """Construct the experimental SubscriptionCLIBackend (M3 PR 16).

    Two-flag opt-in (allow_cli_subscription + acknowledge_unsandboxed_cli)
    plus compliance gate enforcement happen inside the backend's
    `__init__`. Lazy import so loading config alone doesn't pull in
    subprocess management code.
    """
    workspace = kwargs.get("workspace")
    policy = kwargs.get("policy")
    if workspace is None or policy is None:
        raise ValueError(
            "SubscriptionCLIBackend requires `workspace: Path` and "
            "`policy: CheatResistancePolicy` kwargs."
        )
    project_dir = kwargs.get("project_dir")

    from crucible.agents.cli_subscription_backend import (
        SubscriptionCLIBackend,
        SubscriptionCLIBackendError,
    )

    return SubscriptionCLIBackend(
        cli_config=config.cli_subscription,
        experimental=config.experimental,
        policy=policy,
        workspace=Path(workspace),
        project_dir=Path(project_dir) if project_dir else None,
    )
