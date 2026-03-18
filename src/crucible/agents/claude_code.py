"""Claude Code agent — uses the official Claude Agent SDK to generate edits."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    ResultMessage,
    TextBlock,
    query,
)

from crucible.agents.base import AgentInterface, AgentResult

logger = logging.getLogger(__name__)

DEFAULT_AGENT_TIMEOUT = 600

SYSTEM_PROMPT = (
    "You are an elite performance optimization agent. "
    "Your ONLY goal: maximize the target metric improvement.\n\n"

    "## CAN\n"
    "- Use tools: Read, Edit, Write, Glob, Grep\n"
    "- Replace algorithms entirely (e.g., O(n^2) → O(n log n))\n"
    "- Restructure code, change data structures, rewrite functions\n"
    "- Make bold, aggressive changes when the metric is stagnant\n\n"

    "## CANNOT\n"
    "- Run or execute any scripts (the platform runs them automatically)\n"
    "- Access shell, terminal, or subprocess\n"
    "- Modify readonly or hidden files\n"
    "- Skip making changes — you MUST edit code every iteration\n"
    "- Say \"I've exhausted all options\" or \"There's nothing more to try\" "
    "— there are ALWAYS more approaches\n"
)


# Sensitive file patterns — hardcoded, not configurable (prevents agent self-escalation)
_SENSITIVE_DIR_PATTERNS: frozenset[str] = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".azure", ".gcloud",
})

_SENSITIVE_FILE_PREFIXES: frozenset[str] = frozenset({
    ".env",
})


def _is_sensitive_path(rel: str) -> bool:
    """Return True if the relative path matches any sensitive pattern.

    Checks:
    - Any path component matches a sensitive directory name exactly
    - The filename starts with a sensitive file prefix (.env, .env.local, etc.)
    """
    parts = Path(rel).parts
    for part in parts:
        if part in _SENSITIVE_DIR_PATTERNS:
            return True
    filename = parts[-1] if parts else ""
    for prefix in _SENSITIVE_FILE_PREFIXES:
        if filename == prefix or filename.startswith(prefix + "."):
            return True
    return False


def _resolve_rel_path(raw: str, workspace: Path) -> str | None:
    """Resolve a tool input path to a relative path within the workspace.

    Returns the normalized relative path, or None if outside workspace.
    """
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        try:
            rel = str(p.relative_to(workspace))
        except ValueError:
            return None
    else:
        rel = str(p)
    return rel.removeprefix("./")


def _make_file_hooks(
    hidden: set[str], editable: set[str], workspace: Path
) -> dict[str, list[HookMatcher]]:
    """Create PreToolUse hooks that enforce file access policy.

    - Hidden files: deny all access (read + write)
    - Write tools (Edit/Write): only allow editable files (whitelist)
    - Read tools (Read/Glob/Grep): allow all non-hidden files
    """
    _write_tools = {"Edit", "Write"}

    async def pre_tool_use_hook(hook_input: dict, match: str | None, context: Any) -> dict:
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        raw = tool_input.get("file_path") or tool_input.get("path") or ""
        rel = _resolve_rel_path(raw, workspace)

        if not rel:
            return {}

        # Deny all access to hidden files
        if rel in hidden:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Access denied: {rel} is a hidden platform-managed file. "
                        "Do NOT attempt to read, write, or create this file."
                    ),
                }
            }

        # Deny read access to sensitive credential files
        _read_tools = {"Read", "Glob", "Grep"}
        if tool_name in _read_tools and _is_sensitive_path(rel):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Access denied: {rel} matches sensitive file pattern. "
                        "Crucible does not allow reading credential or key files."
                    ),
                }
            }

        # For write tools, only allow editable files
        if tool_name in _write_tools and rel not in editable:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Write denied: {rel} is not in the editable files list. "
                        "You can only modify files listed as editable."
                    ),
                }
            }

        return {}

    return {
        "PreToolUse": [
            HookMatcher(hooks=[pre_tool_use_hook]),
        ],
    }


class ClaudeCodeAgent(AgentInterface):
    def __init__(
        self,
        timeout: int = DEFAULT_AGENT_TIMEOUT,
        model: str | None = None,
        system_prompt_file: str | None = None,
        hidden_files: set[str] | None = None,
        editable_files: set[str] | None = None,
        language: str | None = None,
    ):
        self.timeout = timeout
        self.model = model
        self.system_prompt_file = system_prompt_file
        self.hidden_files: set[str] = hidden_files or set()
        self.editable_files: set[str] = editable_files or set()
        self.language = language

    def get_system_prompt(self, workspace: Path) -> str:
        """Return system prompt: custom file content or default."""
        if self.system_prompt_file:
            prompt_path = workspace / ".crucible" / self.system_prompt_file
            if prompt_path.exists():
                prompt = prompt_path.read_text().strip()
                if self.language:
                    prompt += f"\n\nWrite ALL your summaries and descriptions in {self.language}."
                return prompt
        prompt = SYSTEM_PROMPT
        if self.language:
            prompt += f"\n\nWrite ALL your summaries and descriptions in {self.language}."
        return prompt

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
            return await asyncio.wait_for(
                self._run_query(prompt, workspace),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            return AgentResult(modified_files=[], description="claude agent timed out")
        finally:
            if saved is not None:
                os.environ["CLAUDECODE"] = saved

    async def _run_query(self, prompt: str, workspace: Path) -> AgentResult:
        start = time.monotonic()

        hooks = (
            _make_file_hooks(self.hidden_files, self.editable_files, workspace)
            if self.hidden_files or self.editable_files
            else None
        )
        options = ClaudeAgentOptions(
            system_prompt=self.get_system_prompt(workspace),
            permission_mode="bypassPermissions",
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep"],
            model=self.model,
            cwd=workspace,
            hooks=hooks,
        )

        description = "no description"
        last_text = ""
        all_text_parts: list[str] = []

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            last_text = block.text.strip()
                            all_text_parts.append(block.text.strip())
                            # Stream agent output for visibility
                            for line in block.text.splitlines():
                                logger.debug(f"  {line}")

                elif isinstance(message, ResultMessage):
                    if message.is_error:
                        duration = time.monotonic() - start
                        agent_output = "\n".join(all_text_parts) if all_text_parts else None
                        return AgentResult(
                            modified_files=[],
                            description=f"agent error: {message.result or 'unknown'}",
                            duration_seconds=duration,
                            agent_output=agent_output,
                        )
        except TimeoutError:
            duration = time.monotonic() - start
            agent_output = "\n".join(all_text_parts) if all_text_parts else None
            return AgentResult(
                modified_files=[], description="claude agent timed out",
                duration_seconds=duration,
                agent_output=agent_output,
            )

        if last_text:
            description = _clean_description(last_text)

        # Detect modified files via git
        all_files = _detect_modified_files(workspace)

        duration = time.monotonic() - start
        if not all_files:
            logger.info("[agent] no files changed")
        else:
            logger.info(f"[agent] modified: {[str(f) for f in all_files]}")
        agent_output = "\n".join(all_text_parts) if all_text_parts else None
        return AgentResult(
            modified_files=all_files, description=description,
            duration_seconds=duration,
            agent_output=agent_output,
        )


def _clean_description(text: str) -> str:
    """Extract clean description from agent output."""
    line = text.split("\n")[0]
    line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)  # strip **bold**
    line = re.sub(r"^(Change|Summary|Description|Edit)\s*:\s*", "", line, flags=re.IGNORECASE)
    return line.strip()[:200]


def _detect_modified_files(workspace: Path) -> list[Path]:
    """Use git to find changed and untracked files."""
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
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
