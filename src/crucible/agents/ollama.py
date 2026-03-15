"""Ollama agent — uses Ollama REST API for local LLM inference."""

from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    import requests
except ImportError:
    raise ImportError(
        "Ollama backend requires 'requests'. Install with: pip install requests"
    )

from crucible.agents.base import AgentInterface, AgentResult
from crucible.results import UsageInfo

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> str:
    """Extract JSON from model output, stripping think tags and markdown fences."""
    import re
    # Strip <think>...</think> blocks (reasoning models like deepseek-r1)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
    # Find first { to last } as JSON
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text.strip()

OLLAMA_SYSTEM_PROMPT = (
    "You are a code optimization agent. You receive code files and must "
    "propose edits to improve a metric.\n\n"
    "RESPOND ONLY WITH VALID JSON in this exact format:\n"
    '{"edits": [{"file": "filename.py", "search": "exact text to find", '
    '"replace": "replacement text"}], "description": "one line summary"}\n\n'
    "Rules:\n"
    "- search must be an EXACT substring of the current file content\n"
    "- Make ONE focused change per edit\n"
    "- description must be under 120 characters\n"
)


class OllamaAgent(AgentInterface):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: int = 600,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def capabilities(self) -> set[str]:
        return {"edit"}

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        try:
            return self._call_ollama(prompt, workspace)
        except Exception as e:
            return AgentResult(modified_files=[], description=f"ollama error: {e}")

    def _read_workspace_files(self, workspace: Path) -> str:
        """Read all .py files in workspace for context."""
        parts = []
        for f in sorted(workspace.rglob("*.py")):
            rel = f.relative_to(workspace)
            if str(rel).startswith(".") or "__pycache__" in str(rel):
                continue
            try:
                content = f.read_text()
                parts.append(f"--- {rel} ---\n{content}")
            except Exception:
                continue
        return "\n\n".join(parts)

    def _call_ollama(self, prompt: str, workspace: Path) -> AgentResult:
        # The prompt already contains file contents from assemble_with_files()
        # in context.py. Do NOT re-read workspace files here — it's redundant
        # and would leak hidden files.

        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"num_predict": 8192},
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        content = data["message"]["content"]
        usage = UsageInfo(
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            estimated_cost_usd=0.0,  # local model = free
        )
        duration = data.get("total_duration", 0) / 1e9

        # Parse structured JSON edits
        # Strip <think>...</think> tags from reasoning models (e.g. deepseek-r1)
        parsed_content = _extract_json(content)
        try:
            parsed = json.loads(parsed_content)
            edits = parsed.get("edits", [])
            description = parsed.get("description", "ollama edit")
        except json.JSONDecodeError:
            logger.warning("Ollama returned non-JSON output, no edits applied")
            logger.debug("Raw output: %s", content[:500])
            return AgentResult(
                modified_files=[],
                description="ollama: non-JSON response",
                usage=usage,
                duration_seconds=duration,
            )

        modified = self._apply_edits(edits, workspace)

        return AgentResult(
            modified_files=modified,
            description=description[:200],
            usage=usage,
            duration_seconds=duration,
        )

    def _apply_edits(self, edits: list[dict], workspace: Path) -> list[Path]:
        """Apply search/replace edits to workspace files."""
        modified: list[Path] = []
        for edit in edits:
            filepath = workspace / edit.get("file", "")
            search = edit.get("search", "")
            replace = edit.get("replace", "")
            if not filepath.exists() or not search:
                continue
            content = filepath.read_text()
            if search in content:
                new_content = content.replace(search, replace, 1)
                filepath.write_text(new_content)
                modified.append(Path(edit["file"]))
                logger.debug(f"Applied edit to {edit['file']}")
            else:
                logger.warning(f"Search string not found in {edit['file']}")
        return modified
