from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from crucible.results import UsageInfo


class AgentErrorType(Enum):
    AUTH = "auth"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class AgentResult:
    modified_files: list[Path]
    description: str
    usage: UsageInfo | None = None
    duration_seconds: float | None = None
    agent_output: str | None = None
    error_type: AgentErrorType | None = None


class AgentInterface(ABC):
    @abstractmethod
    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        ...

    def capabilities(self) -> set[str]:
        """Return capabilities this backend supports."""
        return {"read", "edit", "write", "glob", "grep"}
