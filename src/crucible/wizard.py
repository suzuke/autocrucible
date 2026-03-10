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
  name, metric_name, metric_direction, editable_files, timeout_seconds,
  and "architecture_guards" — a list of code-enforced constraints to embed in evaluate.py
  (e.g. "verify neural network is called during decision", "cap editable LOC at 500",
  "ban more than 5 hand-written heuristic functions").
- "uncertain": a list of at most 3 items where you need clarification. Each item has:
  - "param": the parameter name
  - "question": a clear question for the user
  - "choices": a list of options, each with "label" and "explanation"

When the user specifies an algorithmic approach (e.g. "AlphaZero", "genetic algorithm",
"reinforcement learning"), you MUST infer architecture_guards that prevent the agent
from bypassing that approach with hand-coded rules. The metric alone is not enough —
the agent will find shortcuts if constraints are only stated in text.
Do not include any text outside the JSON object.
"""

GENERATE_SYSTEM_PROMPT = """\
You are an experiment scaffold generator. The user will provide a description and resolved decisions.
Return ONLY valid JSON with two keys:
- "files": a dict mapping relative file paths to their string contents. Must include:
  .crucible/config.yaml, .crucible/program.md, and any source files needed.
- "summary": a one-line summary of the generated experiment.
Do not include any text outside the JSON object.

## Architecture Guards (CRITICAL)

The evaluate.py you generate MUST include code-enforced architecture constraints.
The agent will try to maximize the metric by any means — if you only state constraints
in program.md, the agent WILL bypass them. Constraints must be checked in evaluate.py
(which is readonly) and violations must result in metric penalties.

Include these guards in evaluate.py as appropriate:

1. **Code complexity cap**: Use `ast` or line counting on the editable file(s).
   If LOC exceeds a threshold (e.g. 2x the initial size), apply a penalty multiplier.

2. **Required module usage**: If the experiment specifies an approach (e.g. neural network,
   genetic algorithm), verify at runtime that the core module is actually used in
   decision-making — not bypassed by hand-coded rules. For example, instrument the
   model's forward() call and check it was invoked during evaluation.

3. **Banned patterns**: If the approach should learn behavior (not hard-code it),
   use `ast.parse` on the editable file to detect excessive hand-written heuristic
   functions (e.g. more than N function definitions beyond the expected API).

4. **Decision attribution**: Where possible, have the agent's interface return metadata
   about how the decision was made, and verify the stated approach was actually used.

Apply penalties as a multiplier on the primary metric (e.g. `metric *= 0.3` for
violations) rather than zeroing it out, so the agent still gets gradient signal.
"""

GITIGNORE_CONTENT = """\
results.tsv
run.log
__pycache__/
*.pyc
.venv/
uv.lock
"""


def _extract_json(text: str) -> str:
    """Extract JSON from text that may be wrapped in markdown code fences."""
    import re

    # Try raw text first
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped

    # Extract from ```json ... ``` or ``` ... ```
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if m:
        return m.group(1).strip()

    return stripped


def _call_claude(prompt: str, system_prompt: str = "") -> str:
    """Bridge async Claude Agent SDK call to sync."""
    try:
        raw = asyncio.run(_call_claude_async(prompt, system_prompt))
        return _extract_json(raw)
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
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"Claude returned non-JSON: {raw[:200]}")
            raise

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
