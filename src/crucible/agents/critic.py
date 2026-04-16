"""Critic agent — lightweight analysis of experiment history before each iteration."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from crucible.results import ExperimentRecord

logger = logging.getLogger(__name__)

CRITIC_SYSTEM_PROMPT = (
    "You are a concise experiment analysis critic. "
    "Your job is to analyze experiment history and provide actionable guidance "
    "for the next iteration.\n\n"
    "Output EXACTLY this format (no other text):\n\n"
    "DIAGNOSIS: <one line — why the last attempt failed or succeeded>\n"
    "PATTERN: <one line — what pattern you see across all attempts>\n"
    "RECOMMENDATION: <one line — specific, actionable next step>\n"
    "AVOID: <one line — what NOT to try based on history>\n"
)


def _format_history_for_critic(
    records: list[ExperimentRecord],
    agent_log: str | None = None,
) -> str:
    """Format experiment records into a compact prompt for the critic."""
    lines = ["## Experiment History"]
    for r in records[-10:]:
        symbol = {"keep": "✓", "crash": "💥", "discard": "✗"}.get(r.status, "?")
        line = f"{symbol} metric={r.metric_value} | {r.description}"
        if r.diff_text:
            line += f"\n```diff\n{r.diff_text}\n```"
        lines.append(line)

    if agent_log:
        lines.append(f"\n## Last Agent Reasoning (excerpt)\n{agent_log[-2000:]}")

    return "\n".join(lines)


class CriticAgent:
    """Lightweight critic that analyzes history and produces recommendations."""

    def __init__(self, model: str = "haiku", timeout: int = 30):
        self.model = model
        self.timeout = timeout

    def analyze(
        self,
        records: list[ExperimentRecord],
        workspace: Path,
        iteration: int,
        agent_log: str | None = None,
    ) -> str | None:
        """Run critic analysis. Returns structured recommendation or None on failure."""
        if not records:
            return None

        prompt = _format_history_for_critic(records, agent_log)
        try:
            return asyncio.run(self._run(prompt, workspace))
        except Exception as e:
            logger.warning("[critic] error: %s", e)
            return None

    async def _run(self, prompt: str, workspace: Path) -> str | None:
        saved = os.environ.pop("CLAUDECODE", None)
        try:
            return await asyncio.wait_for(
                self._query(prompt, workspace), timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("[critic] timed out")
            return None
        finally:
            if saved is not None:
                os.environ["CLAUDECODE"] = saved

    async def _query(self, prompt: str, workspace: Path) -> str | None:
        options = ClaudeAgentOptions(
            system_prompt=CRITIC_SYSTEM_PROMPT,
            permission_mode="bypassPermissions",
            allowed_tools=[],  # No tools — text-only analysis
            model=self.model,
            cwd=workspace,
        )

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        result_text += block.text.strip() + "\n"

        return result_text.strip() if result_text.strip() else None
