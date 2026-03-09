"""Claude Code agent — uses the official Claude Agent SDK to generate edits."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from crucible.agents.base import AgentInterface, AgentResult

logger = logging.getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 600

SYSTEM_PROMPT = (
    "You are an autonomous code optimization agent. "
    "You MUST use the Read tool to examine files, then use the Edit tool to modify them. "
    "Do NOT just describe or explain changes — you must actually edit the files using tools. "
    "After editing, output a one-line summary of what you changed."
)


class ClaudeCodeAgent(AgentInterface):
    def __init__(self, timeout: int = DEFAULT_AGENT_TIMEOUT, model: str | None = None):
        self.timeout = timeout
        self.model = model

    def generate_edit(self, prompt: str, workspace: Path) -> AgentResult:
        """Run Claude Agent SDK to generate code edits.

        Uses asyncio.run() to bridge the sync interface with the async SDK.
        """
        try:
            return asyncio.run(self._generate_edit_async(prompt, workspace))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return AgentResult(modified_files=[], description=f"agent error: {e}")

    async def _generate_edit_async(self, prompt: str, workspace: Path) -> AgentResult:
        # Strip CLAUDECODE env var to allow running inside a Claude Code session.
        # The SDK spawns claude CLI as a subprocess which inherits the parent env;
        # CLAUDECODE=1 blocks nested sessions, so we must remove it.
        saved = os.environ.pop("CLAUDECODE", None)
        try:
            return await self._run_query(prompt, workspace)
        finally:
            if saved is not None:
                os.environ["CLAUDECODE"] = saved

    async def _run_query(self, prompt: str, workspace: Path) -> AgentResult:
        options = ClaudeAgentOptions(
            system_prompt=SYSTEM_PROMPT,
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep"],
            model=self.model,
            cwd=workspace,
        )

        description = "no description"
        last_text = ""

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            last_text = block.text.strip()
                            # Stream agent output for visibility
                            for line in block.text.splitlines():
                                logger.debug(f"  {line}")

                elif isinstance(message, ResultMessage):
                    if message.is_error:
                        return AgentResult(
                            modified_files=[],
                            description=f"agent error: {message.result or 'unknown'}",
                        )
        except TimeoutError:
            return AgentResult(modified_files=[], description="claude agent timed out")

        if last_text:
            description = last_text.split("\n")[0][:200]

        # Detect modified files via git
        all_files = _detect_modified_files(workspace)

        if not all_files:
            logger.info("[agent] no files changed")
        else:
            logger.info(f"[agent] modified: {[str(f) for f in all_files]}")
        return AgentResult(modified_files=all_files, description=description)


def _detect_modified_files(workspace: Path) -> list[Path]:
    """Use git to find changed and untracked files."""
    diff_result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=workspace, capture_output=True, text=True,
    )
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=workspace, capture_output=True, text=True,
    )
    changed = diff_result.stdout.strip().splitlines()
    untracked = untracked_result.stdout.strip().splitlines()
    changed = [f for f in changed if "__pycache__/" not in f]
    untracked = [f for f in untracked if "__pycache__/" not in f]
    return [Path(f) for f in changed + untracked if f]
