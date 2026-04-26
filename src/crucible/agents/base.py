from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

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
    # M3 PR 16: backend-specific metadata propagated to AttemptNode.
    # Used by `cli_subscription` adapters to record cli_binary_path,
    # cli_version, cli_argv (with secrets redacted), isolation tag,
    # provider_safety_filter_active tri-state, etc. Other backends
    # may leave this empty.
    backend_metadata: dict[str, Any] = field(default_factory=dict)


class AgentInterface(ABC):
    @abstractmethod
    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        ...

    def capabilities(self) -> set[str]:
        """Return capabilities this backend supports.

        Default: {"read", "edit", "write", "glob", "grep"} — the
        smolagents/claude-code default tool surface. CLI subscription
        backends should override to declare degraded ACL via
        `{"agent_loop_external", "host_fs_visible"}` (M3 PR 16) so
        callers can branch on isolation strength.
        """
        return {"read", "edit", "write", "glob", "grep"}
