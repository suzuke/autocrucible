"""smolagents Model wrapper for `claude_agent_sdk` — M3 PR 19.

This wrapper lets the smolagents `AgentBackend` (M2 PR 13) drive Claude
via the user's CC subscription auth (OAuth in `~/.claude/`) instead of
an Anthropic API key.

**Critical design pin (reviewer round 1, Q3)**:

`claude_agent_sdk.query()` is **NOT a token completion API.** It is
a complete agent product: it runs its own agent loop, executes its own
tools internally (Read/Edit/Write/Glob/Grep/Bash), and returns the
post-agent text. If used naively as smolagents' Model, smolagents
sees the text *after* the SDK already ran a full agent loop with its
own tools — re-creating the §3.3 agent-loop-in-agent-loop problem and
silently voiding the smolagents `CheatResistancePolicy` ACL.

The wrapper configures the SDK as a **degenerate single-turn text
generator** so smolagents' tool boundary is the only one that fires:

  - `allowed_tools=[]`         — no SDK-side tools usable
  - `disallowed_tools=[...]`   — exhaustive deny list, defense in depth
  - `max_turns=1`              — no internal looping
  - `can_use_tool=` callback   — returns False for any tool, defense in depth

This invariant is locked in by `test_sdk_is_invoked_with_no_internal_tools`
in `tests/test_smolagents_claude_sdk_model.py`. If a future SDK update
adds a default tool that bypasses `disallowed_tools`, the invariant
breaks AND the test still passes — re-verify against
`claude_agent_sdk.__version__` before relying on this in production.

**Anthropic ToS**: `claude_agent_sdk` is the official Python SDK,
designed for first-party CC + Claude Code Skills products. Anthropic
has NOT publicly endorsed using `claude_agent_sdk` outside those
products. Operators should review their CC ToS before relying on this
in production. This wrapper is a transitional shim; remove when (a)
smolagents ships native subscription auth, or (b) Anthropic publishes
a token-completion API path with OAuth credential support.

**Cost reporting nuance**: SDK's `ResultMessage.total_cost_usd` is an
estimate of *equivalent API pricing* — what the call would have cost
on metered API auth. On subscription, you do NOT pay that. AttemptNode
records this as `usage_source="oauth_estimated"` (a new value) to
disambiguate from `"api"` (actual metered cost) and avoid users
thinking they're being double-billed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from smolagents import ChatMessage

logger = logging.getLogger(__name__)


# Tools the SDK might surface internally. Disallow exhaustively so a
# future SDK release that adds a new default tool doesn't slip through.
_SDK_DISALLOWED_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "NotebookEdit",
    "MultiEdit",
    "BashOutput",
    "KillShell",
)


class ClaudeAgentSDKAuthError(RuntimeError):
    """Raised when `claude_agent_sdk` can't find OAuth credentials.

    Maps to `AgentErrorType.AUTH` in the surrounding orchestrator so
    the run loop classifies it correctly. Carries actionable next step
    in the message.
    """

    def __init__(self, original_exc: Exception) -> None:
        super().__init__(
            f"Not logged in to Claude Code. Run `claude login` to authenticate, "
            f"or switch to `provider: anthropic` with an explicit API key. "
            f"Original error: {original_exc}"
        )
        self.original_exc = original_exc


class ClaudeAgentSDKModel:
    """smolagents `Model`-like adapter that drives `claude_agent_sdk`.

    Implements the `generate(messages, ...)` interface smolagents'
    ToolCallingAgent expects. Returns a `ChatMessage` with the model's
    text response — smolagents then parses for tool calls and dispatches
    via its OWN tools (which are ACL-enforced via `CheatResistancePolicy`).
    """

    def __init__(
        self,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        max_thinking_tokens: int | None = None,
    ) -> None:
        # Lazy-validate the SDK is importable; raise loud if not.
        try:
            import claude_agent_sdk  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "ClaudeAgentSDKModel requires `claude-agent-sdk` (>=0.1.50, <0.2). "
                "Already pinned via crucible's base dependencies."
            ) from exc

        self._model = model
        self._max_thinking_tokens = max_thinking_tokens

    # ------------------------------------------------------------------
    # smolagents Model interface
    # ------------------------------------------------------------------

    # Match smolagents.Model attributes for ToolCallingAgent compat.
    supports_stop_parameter = False

    def generate(
        self,
        messages: list[Any],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs,
    ) -> "ChatMessage":
        """Generate one response. Synchronous; drains the async SDK stream."""
        from smolagents import ChatMessage
        from smolagents.models import MessageRole

        prompt = self._format_prompt(messages, tools_to_call_from)
        try:
            text, raw_events = asyncio.run(self._async_call(prompt))
        except RuntimeError as exc:
            # smolagents may one day move to async; if so, this wrapper
            # needs an async path. Fail loud for the dev who hits it.
            if "already running" in str(exc) or "cannot be called from a running" in str(exc):
                raise RuntimeError(
                    "ClaudeAgentSDKModel cannot be called from inside an existing "
                    "asyncio loop. The current smolagents version is sync; if a "
                    "future version becomes async, this wrapper needs an async "
                    "path. See module docstring."
                ) from exc
            raise

        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=text,
            raw={"sdk_events": raw_events},
        )

    # smolagents may also call __call__; mirror generate
    def __call__(self, messages: list[Any], **kwargs) -> "ChatMessage":
        return self.generate(messages, **kwargs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _format_prompt(
        self,
        messages: list[Any],
        tools_to_call_from: list[Any] | None,
    ) -> str:
        """Flatten smolagents messages into a single prompt string.

        The SDK wants prompt as a string. smolagents passes messages
        list with role + content. We render them in
        `<role>:\n<content>\n` form. Tool descriptions (when smolagents
        is in tool-calling mode) are appended as part of the prompt
        body — Claude sees the smolagents tool-calling system prompt
        verbatim.
        """
        parts = []
        for msg in messages:
            # Both `dict` and `ChatMessage` shapes possible
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                content = msg.get("content", "")
            else:
                role = getattr(getattr(msg, "role", None), "value", None) or str(getattr(msg, "role", "user"))
                content = getattr(msg, "content", "")
            if isinstance(content, list):
                # smolagents content can be list of {type, text} blocks
                content = "\n".join(
                    item.get("text", "") if isinstance(item, dict) else str(item)
                    for item in content
                )
            parts.append(f"{role}:\n{content}\n")
        return "\n".join(parts)

    async def _async_call(self, prompt: str) -> tuple[str, list[dict]]:
        """Run the SDK as a degenerate text generator and collect output.

        Reviewer round 1 critical pin: SDK is configured with
        allowed_tools=[], max_turns=1, exhaustive disallowed_tools, and
        a `can_use_tool=lambda: False` callback. This forces the SDK to
        return a single text response with NO internal tool execution —
        smolagents' ACL boundary is the only one that fires.
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=self._model,
            allowed_tools=[],
            disallowed_tools=list(_SDK_DISALLOWED_TOOLS),
            max_turns=1,
            can_use_tool=_deny_all_tools,
            permission_mode="default",
        )

        text_parts: list[str] = []
        events: list[dict] = []
        try:
            async for event in query(prompt=prompt, options=options):
                # Record event metadata for AttemptNode debug
                events.append({"type": type(event).__name__})
                if isinstance(event, AssistantMessage):
                    for block in getattr(event, "content", []):
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(event, ResultMessage):
                    # End of stream; capture cost estimate for ledger
                    cost = getattr(event, "total_cost_usd", None)
                    if cost is not None:
                        events[-1]["estimated_cost_usd"] = cost
        except FileNotFoundError as exc:
            raise ClaudeAgentSDKAuthError(exc) from exc
        except OSError as exc:
            # SDK raises various OSError flavors when credentials are
            # missing / unreadable. Classify as auth.
            raise ClaudeAgentSDKAuthError(exc) from exc

        text = "".join(text_parts).strip()
        return text, events


async def _deny_all_tools(tool_name: str, tool_input: dict, context: Any) -> dict:
    """can_use_tool callback that unconditionally denies any tool call.

    Defense-in-depth: even if the SDK ignores allowed_tools=[] for
    some reason, this callback ensures no tool actually runs.
    Returns the documented `{"behavior": "deny", "message": ...}` shape.
    """
    return {
        "behavior": "deny",
        "message": (
            "ClaudeAgentSDKModel deliberately denies all internal tool "
            "calls. The smolagents ACL boundary is the only place where "
            "tools should execute. See module docstring for invariant."
        ),
    }
