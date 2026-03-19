"""Agent factory for creating agent instances from config."""

from crucible.agents.base import AgentInterface, AgentResult
from crucible.config import AgentConfig

__all__ = ["AgentInterface", "AgentResult", "create_agent"]


def create_agent(config: AgentConfig, **kwargs) -> AgentInterface:
    """Create an agent instance based on config.type.

    Config fields (model, language) are used as defaults; explicit kwargs override.
    """
    defaults: dict = {}
    if config.model is not None:
        defaults["model"] = config.model
    if config.language is not None:
        defaults["language"] = config.language
    merged = {**defaults, **kwargs}

    if config.type == "claude-code":
        from crucible.agents.claude_code import ClaudeCodeAgent

        return ClaudeCodeAgent(**merged)
    else:
        raise ValueError(f"Unknown agent type: {config.type}")
