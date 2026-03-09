from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentResult:
    modified_files: list[Path]
    description: str


class AgentInterface(ABC):
    @abstractmethod
    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        ...
