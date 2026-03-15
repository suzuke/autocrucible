"""Agent factory for creating agent instances from config."""

from crucible.agents.base import AgentInterface, AgentResult
from crucible.config import AgentConfig

__all__ = ["AgentInterface", "AgentResult", "create_agent"]


def create_agent(config: AgentConfig, **kwargs) -> AgentInterface:
    """Create an agent instance based on config.type."""
    if config.type == "claude-code":
        from crucible.agents.claude_code import ClaudeCodeAgent

        return ClaudeCodeAgent(**kwargs)
    elif config.type == "ollama":
        from crucible.agents.ollama import OllamaAgent

        return OllamaAgent(
            model=config.model or "qwen2.5-coder:32b",
            base_url=config.base_url or "http://localhost:11434",
            timeout=kwargs.get("timeout", 600),
        )
    else:
        raise ValueError(f"Unknown agent type: {config.type}")
