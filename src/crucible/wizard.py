"""Experiment wizard — analyzes descriptions and generates project scaffolds."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

logger = logging.getLogger(__name__)

ANALYZE_SYSTEM_PROMPT = """\
You are an experiment design assistant. The user will describe an optimization experiment.
Return ONLY valid JSON with two keys:
- "inferred": a dict of parameters you can confidently determine from the description, including:
  name, metric_name, metric_direction, editable_files, timeout_seconds
- "uncertain": a list of at most 3 items where you need clarification. Each item has:
  - "param": the parameter name
  - "question": a clear question for the user
  - "choices": a list of options, each with "label" and "explanation"
Do not include any text outside the JSON object.
"""

GENERATE_SYSTEM_PROMPT = """\
You are an experiment scaffold generator. The user will provide a description and resolved decisions.
Return ONLY valid JSON with two keys:
- "files": a dict mapping relative file paths to their string contents. Must include:
  .crucible/config.yaml, .crucible/program.md, and any source files needed.
- "summary": a one-line summary of the generated experiment.
Do not include any text outside the JSON object.
"""

GITIGNORE_CONTENT = """\
results.tsv
run.log
__pycache__/
*.pyc
.venv/
uv.lock
"""


def _call_claude(prompt: str, system_prompt: str = "") -> str:
    """Bridge async Claude Agent SDK call to sync."""
    try:
        return asyncio.run(_call_claude_async(prompt, system_prompt))
    except Exception as e:
        logger.error(f"Claude call failed: {e}")
        raise


async def _call_claude_async(prompt: str, system_prompt: str) -> str:
    saved = os.environ.pop("CLAUDECODE", None)
    try:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            permission_mode="bypassPermissions",
            allowed_tools=[],
            cwd=Path.cwd(),
        )
        last_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        last_text = block.text.strip()
        return last_text or "{}"
    finally:
        if saved is not None:
            os.environ["CLAUDECODE"] = saved


class ExperimentWizard:
    """Two-phase wizard: analyze a description, then generate project files."""

    def analyze(self, description: str) -> dict:
        """Phase 1: send description to Claude, return parsed JSON with inferred + uncertain."""
        raw = _call_claude(description, system_prompt=ANALYZE_SYSTEM_PROMPT)
        return json.loads(raw)

    def generate(self, description: str, decisions: dict, dest: Path) -> str:
        """Phase 2: send decisions to Claude, write files, return summary."""
        prompt = json.dumps({"description": description, "decisions": decisions})
        raw = _call_claude(prompt, system_prompt=GENERATE_SYSTEM_PROMPT)
        result = json.loads(raw)

        # Write each file
        for rel_path, content in result["files"].items():
            full_path = dest / rel_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

        # Create .gitignore
        (dest / ".gitignore").write_text(GITIGNORE_CONTENT)

        return result["summary"]
