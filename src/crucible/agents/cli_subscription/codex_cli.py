"""CodexCLIAdapter — wraps the OpenAI Codex CLI (M3 PR 16a).

Spec framing & reviewer round-1 binding requirements:

- **Spec §3.3 (CLI is a complete agent product)**: codex `exec` runs its
  own internal agent loop with its own tools. We invoke it as a
  non-conversational, ephemeral, scratch-isolated subprocess and parse
  the JSONL event stream. Crucible's scratch + copy-back is the outer
  blast-radius limiter; codex's `--sandbox workspace-write` is the
  inner one (defense in depth).
- **Spec §INV-3 belt-and-braces (no CodeAct re-enablement)**: forbidden
  flags listed in `_FORBIDDEN_FLAGS`; absence is locked in by
  `test_codex_argv_excludes_forbidden_flags`. If a future codex
  release introduces an `--exec-shell` style flag, it MUST be added to
  `_FORBIDDEN_FLAGS` first so the trade-off is visible in review.
- **Reviewer round 1 #1 (sandbox mode)**: use `--sandbox workspace-write`,
  NOT `read-only`. Read-only mode prevents codex from writing the
  edited file, breaking the edit/evaluate loop the agent backend
  expects. workspace-write keeps codex's writes scoped to its `--cd`
  (the scratch dir); Crucible's `copy_editable_changes_back` then
  filters by `CheatResistancePolicy` editable list before committing
  to the real workspace.
- **Reviewer round 1 #4 (typed auth error)**: parse_output raises a
  typed `CodexCLIAuthError` when codex emits a known auth-failure
  phrase. The backend isinstance-checks it to map to
  `AgentErrorType.AUTH`. Mirrors the `ClaudeAgentSDKAuthError` pattern
  pinned by PR 19 round 2 — auth classification is by exception type,
  not by coincidental substring in a generic exception's message.
- **Reviewer round 1 #5 (phantom-command-free errors)**: every error
  message references a real, runnable command (`codex login`) or a
  config knob (`agent.cli_subscription.adapter`). No fictitious flags.

JSONL event schema (verified against codex source `core/src/exec_events.rs`
during PR 16a spike, fixture in `tests/fixtures/codex_exec_quota_exceeded.jsonl`):

  Event types (top-level `type`):
    thread.started, turn.started, item.started, item.completed,
    turn.completed, error, turn.failed

  Item types (inside `item` of item.{started,completed}):
    agent_message (text in `text` field), reasoning,
    command_execution, file_change, web_search

  No `schema_version` field is currently emitted by codex. Schema
  drift detection therefore depends on KNOWN_{EVENT,ITEM}_TYPES
  membership: any unrecognised value sets `unknown_schema=True`,
  classifying the trial as parse_failure in the compliance harness
  (per spec §3.2 ≥99% release / ≥95% admit gate).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)

logger = logging.getLogger(__name__)


# Authoritative event-type set. Source: openai/codex
# `core/src/exec_events.rs` (verified Apr 2026 during PR 16a spike).
KNOWN_EVENT_TYPES = frozenset({
    "thread.started",
    "turn.started",
    "item.started",
    "item.completed",
    "turn.completed",
    "error",
    "turn.failed",
})


# Item kinds carried inside `item.started` / `item.completed`. Spec
# field name is `item_type` in current codex, but we also accept `type`
# defensively in case a future release renames it.
KNOWN_ITEM_TYPES = frozenset({
    "agent_message",
    "reasoning",
    "command_execution",
    "file_change",
    "web_search",
})


# Spec §INV-3 belt-and-braces: flags codex MUST NOT receive from us.
# Some are hypothetical (CodeAct / REPL / eval style) and others are
# real codex flags whose semantics conflict with the constrained
# subscription-backend contract. The regression test
# `test_codex_argv_excludes_forbidden_flags` enforces absence.
_FORBIDDEN_FLAGS = frozenset({
    # Hypothetical re-enablement flags
    "--code-act",
    "--repl",
    "--eval",
    "--shell",
    "--bash",
    "--exec-shell",
    # Real codex flags we deliberately don't pass: bypassing approvals
    # would let codex's internal tool loop run unbounded code outside
    # smolagents' / cli_subscription's outer ACL.
    "--full-auto",
    "--bypass-approvals",
    "--dangerously-skip-permissions",
})


# Auth-failure phrases codex emits. Matched intentionally against an
# explicitly declared set — not coincidental substring matching of
# generic exception messages (PR 19 round 2 lesson). If codex changes
# its instructional copy, this set must be updated to keep auth
# classification accurate.
_AUTH_FAILURE_PHRASES = (
    "Not authenticated",
    "Run `codex login`",
    "ChatGPT authentication required",
    "OAuth credentials missing",
    "OAuth credentials expired",
    "Please sign in to ChatGPT",
)


class CodexCLIAuthError(RuntimeError):
    """Raised when codex emits an auth-failure signal.

    SubscriptionCLIBackend isinstance-checks this to map to
    `AgentErrorType.AUTH`. Carries the literal evidence phrase so the
    operator can see what codex said. The instructional next step
    references only real, runnable commands (`codex login`).
    """

    def __init__(self, evidence: str) -> None:
        super().__init__(
            f"Codex CLI is not authenticated. Run `codex login` to "
            f"sign in, or switch to `agent.cli_subscription.adapter: "
            f"claude-code-cli` if you have a Claude Code subscription. "
            f"Evidence: {evidence!r}"
        )
        self.evidence = evidence


class CodexCLIAdapter(SubscriptionCLIAdapter):
    """Wrap `codex exec --json` as a single-turn ephemeral subprocess.

    Driven by SubscriptionCLIBackend; receives prompt + scratch dir,
    returns parsed text + structured events. Modifies files only inside
    the scratch dir; the backend's copy-back step enforces the outer
    `CheatResistancePolicy` ACL.
    """

    cli_name = "codex-cli"
    default_binary_name = "codex"

    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        # Flag-by-flag rationale lives in the module docstring. Do NOT
        # add any flag listed in _FORBIDDEN_FLAGS without first
        # removing it from that set (and explaining why in the PR).
        return [
            str(self.cli_binary_path),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd", str(ctx.scratch_dir),
            "--sandbox", "workspace-write",
            ctx.prompt,
        ]

    def version_command(self) -> Sequence[str]:
        return [str(self.cli_binary_path), "--version"]

    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        """Parse codex `exec --json` JSONL stream into structured fields.

        Tri-state outcomes:
          - normal           → ParsedAdapterOutput with description
          - schema drift     → unknown_schema=True (compliance: parse_failure)
          - auth failure     → raises CodexCLIAuthError (backend → AUTH)
        """
        description_parts: list[str] = []
        events: list[dict[str, Any]] = []
        tool_was_called = False
        unknown_schema = False
        auth_evidence: str | None = None

        for line_no, line in enumerate(raw.stdout.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue

            # Codex prints a stdin-notice line ("Reading additional input
            # from stdin...") before the JSONL stream when prompt arrives
            # via stdin. Tolerate it but flag stray non-JSON as schema
            # drift if it doesn't match the known prelude.
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(
                    "codex-cli: non-JSON output at line %d: %r", line_no, line
                )
                if any(p in line for p in _AUTH_FAILURE_PHRASES):
                    auth_evidence = line
                # Pre-JSON stdin notice is benign; anything else is drift
                if "Reading additional input" not in line:
                    unknown_schema = True
                continue

            if not isinstance(event, dict):
                unknown_schema = True
                continue

            etype = (event.get("type") or "").lower()
            events.append(event)

            if etype not in KNOWN_EVENT_TYPES:
                unknown_schema = True

            if etype in ("error", "turn.failed"):
                msg = self._extract_error_message(event)
                if msg and any(p in msg for p in _AUTH_FAILURE_PHRASES):
                    auth_evidence = msg

            if etype == "item.completed":
                item = event.get("item") or {}
                item_type = (
                    item.get("item_type") or item.get("type") or ""
                ).lower()
                if item_type and item_type not in KNOWN_ITEM_TYPES:
                    unknown_schema = True
                if item_type == "agent_message":
                    text = item.get("text") or ""
                    if isinstance(text, str) and text:
                        description_parts.append(text)
                elif item_type in (
                    "command_execution", "file_change", "web_search"
                ):
                    tool_was_called = True

        if auth_evidence is not None:
            raise CodexCLIAuthError(auth_evidence)

        description = "".join(description_parts).strip()

        return ParsedAdapterOutput(
            modified_files=[],
            description=description,
            structured_events=events,
            tool_was_called=tool_was_called or None,
            unknown_schema=unknown_schema,
        )

    @staticmethod
    def _extract_error_message(event: dict[str, Any]) -> str:
        """Pull the message out of an `error` or `turn.failed` event.

        Two known shapes (verified against fixture):
          {"type":"error","message":"..."}
          {"type":"turn.failed","error":{"message":"..."}}
        """
        msg = event.get("message")
        if isinstance(msg, str):
            return msg
        nested = event.get("error")
        if isinstance(nested, dict):
            inner = nested.get("message")
            if isinstance(inner, str):
                return inner
        return ""
