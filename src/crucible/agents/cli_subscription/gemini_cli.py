"""GeminiCLIAdapter — wraps Google's Gemini CLI (M3 PR 16b).

Spec framing & design pins (carried forward from PR 16a CodexCLIAdapter):

- **Spec §3.3 (CLI is a complete agent product)**: gemini -p runs its
  own internal agent loop with built-in tools (read_file, etc.). We
  invoke it as a non-interactive headless subprocess and parse the
  stream-json event stream.
- **Spec §INV-3 belt-and-braces (no auto-approval)**: forbidden flags
  listed in `_FORBIDDEN_FLAGS`; absence locked in by
  `test_gemini_argv_excludes_forbidden_flags`. Critical: `--yolo` /
  `-y` auto-approves all tool calls (the gemini analog of codex
  `--full-auto`); `--approval-mode yolo` is the same thing under a
  different surface. Both are forbidden.
- **Reviewer round 1 PR 16a #1 (sandbox mode)**: gemini's `--sandbox`
  is a boolean (less granular than codex's three-mode). We do NOT pass
  `--sandbox` — the blast-radius limiter is Crucible's outer
  scratch + `copy_editable_changes_back`, with the subprocess `cwd`
  pointed at the scratch dir by SubscriptionCLIBackend (`run_subprocess`
  in base.py sets `cwd=ctx.workspace_root`, which the backend points
  at scratch). gemini operates only on its `cwd` by default, so no
  `--include-directories` flag is needed to scope it. `--approval-mode
  default` (NOT `yolo` / `auto_edit`) prevents auto-approval of tool
  calls. `--skip-trust` is required because the scratch dir is
  ephemeral and not a "trusted workspace" in gemini's UX model —
  without it gemini blocks on stdin asking for trust confirmation.
- **Typed auth error**: `GeminiCLIAuthError` raised from `parse_output`
  on declared auth-failure phrases; backend isinstance-checks. Mirrors
  the `CodexCLIAuthError` / `ClaudeAgentSDKAuthError` pattern. PR 16c
  may consolidate these into a `CLISubscriptionAuthError` base class
  if a third instance motivates the abstraction (per PR 16a R2 #4).

stream-json event schema (verified during PR 16b spike, fixture in
`tests/fixtures/gemini_stream_json_tool_call.jsonl`, gemini 0.39.1):

  type=init      session_id, model, timestamp
  type=message   role (user/assistant), content (str), delta (bool)
  type=tool_use  tool_name, tool_id, parameters
  type=tool_result  tool_id, status, output
  type=result    status (success/...), stats { total_tokens,
                  input_tokens, output_tokens, duration_ms,
                  tool_calls, models {...} }

No `schema_version` field is currently emitted; schema drift detection
relies on `KNOWN_EVENT_TYPES` membership. Unknown event types mark
`unknown_schema=True` (compliance harness classifies as parse_failure
per spec §3.2).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
    CLISubscriptionAuthError,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)

logger = logging.getLogger(__name__)


# Gemini stream-json event types (verified Apr 2026 against gemini
# 0.39.1, fixture `tests/fixtures/gemini_stream_json_tool_call.jsonl`).
KNOWN_EVENT_TYPES = frozenset({
    "init",
    "message",
    "tool_use",
    "tool_result",
    "result",
})


# Spec §INV-3 belt-and-braces: flags gemini MUST NOT receive from us.
# Real gemini 0.39.1 flag names verified against `gemini --help`.
_FORBIDDEN_FLAGS = frozenset({
    # Hypothetical re-enablement flags
    "--code-act",
    "--repl",
    "--eval",
    "--shell",
    "--bash",
    "--exec-shell",
    # Real gemini flags. `--yolo` / `-y` auto-approves all tool calls.
    # `--accept-raw-output-risk` suppresses the security warning when
    # raw output (with ANSI escapes) is enabled — defense in depth, we
    # neither enable raw output nor accept the risk silently.
    "--yolo",
    "-y",
    "--accept-raw-output-risk",
    "--raw-output",
})


# Approval modes that AUTO-APPROVE tool calls — these are the
# `--approval-mode <X>` values we MUST NEVER pass. The flag itself is
# allowed (we pass `default`); only specific values are forbidden.
# Spec §INV-3: belt-and-braces against silent re-enablement.
_FORBIDDEN_APPROVAL_MODES = frozenset({
    "yolo",        # auto-approve all tools
    "auto_edit",   # auto-approve edit tools
})


# Auth-failure phrases gemini emits. Matched intentionally against an
# explicitly declared set (PR 19 round 2 lesson — no coincidental
# substring matching). The first entry is verified first-hand against
# gemini 0.39.1 stderr output (PR 16b polish spike: HOME=tmp + empty
# GEMINI_API_KEY → `tests/fixtures/gemini_auth_failure.stderr.txt`).
# The rest are extrapolated from common phrasing for forward-compat
# coverage if gemini's copy changes.
_AUTH_FAILURE_PHRASES = (
    "Please set an Auth method",        # verified: gemini 0.39.1 stderr
    "Not signed in",                    # extrapolated
    "Please sign in",                   # extrapolated
    "Authentication required",          # extrapolated
    "Run `gemini auth`",                # extrapolated
    "GEMINI_API_KEY is not set",        # extrapolated
    "Invalid API key",                  # extrapolated
    "OAuth token expired",              # extrapolated
)


class GeminiCLIAuthError(CLISubscriptionAuthError):
    """Raised when gemini emits an auth-failure signal.

    SubscriptionCLIBackend isinstance-checks the `CLISubscriptionAuthError`
    base to map to `AgentErrorType.AUTH`. Carries the literal evidence
    phrase so the operator can see what gemini said. The instructional
    next step references only real, runnable commands (`gemini auth`).
    """

    def __init__(self, evidence: str) -> None:
        message = (
            f"Gemini CLI is not authenticated. Run `gemini auth` to "
            f"sign in (or set `GEMINI_API_KEY`), or switch to "
            f"`agent.cli_subscription.adapter: claude-code-cli`. "
            f"Evidence: {evidence!r}"
        )
        RuntimeError.__init__(self, message)
        self.evidence = evidence


class GeminiCLIAdapter(SubscriptionCLIAdapter):
    """Wrap `gemini -p` headless mode as a non-conversational subprocess.

    Driven by SubscriptionCLIBackend; receives prompt + scratch dir,
    returns parsed text + structured events. gemini's tool calls run
    on cwd / `--include-directories`; the backend's copy-back step
    enforces the outer `CheatResistancePolicy` ACL.
    """

    cli_name = "gemini-cli"
    default_binary_name = "gemini"

    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        # Flag-by-flag rationale:
        #   -p <prompt>            non-interactive headless mode
        #   -o stream-json         JSONL event stream
        #   --approval-mode default  prompt for approval on every tool
        #                          call. We do NOT pass `yolo` /
        #                          `auto_edit` (see _FORBIDDEN_APPROVAL_MODES).
        #                          NB: in our subprocess context there's no
        #                          interactive prompter, so any tool call
        #                          that requires approval will block /
        #                          fail; the backend's copy-back model
        #                          captures whatever gemini wrote before
        #                          blocking.
        #   --skip-trust           the scratch dir is ephemeral and not
        #                          a "trusted workspace" in gemini's
        #                          model. Without this, gemini's first-run
        #                          UX requires user trust confirmation
        #                          (which would block on stdin in headless
        #                          mode).
        #
        # Do NOT add any flag listed in _FORBIDDEN_FLAGS. Do NOT pass
        # any value listed in _FORBIDDEN_APPROVAL_MODES via
        # `--approval-mode`.
        return [
            str(self.cli_binary_path),
            "-p", ctx.prompt,
            "-o", "stream-json",
            "--approval-mode", "default",
            "--skip-trust",
        ]

    def version_command(self) -> Sequence[str]:
        return [str(self.cli_binary_path), "--version"]

    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        """Parse gemini stream-json JSONL stream.

        Tri-state outcomes:
          - normal           → ParsedAdapterOutput with description
          - schema drift     → unknown_schema=True (compliance: parse_failure)
          - auth failure     → raises GeminiCLIAuthError (backend → AUTH)
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

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(
                    "gemini-cli: non-JSON output at line %d: %r", line_no, line
                )
                if any(p in line for p in _AUTH_FAILURE_PHRASES):
                    auth_evidence = line
                unknown_schema = True
                continue

            if not isinstance(event, dict):
                unknown_schema = True
                continue

            etype = (event.get("type") or "").lower()
            events.append(event)

            if etype not in KNOWN_EVENT_TYPES:
                unknown_schema = True

            # Auth-failure detection — explicit phrase match against a
            # declared set (round 2 lesson).
            if etype == "result":
                # `result` may carry an error message on non-success
                status = (event.get("status") or "").lower()
                if status and status != "success":
                    msg = self._extract_error_message(event)
                    if msg and any(p in msg for p in _AUTH_FAILURE_PHRASES):
                        auth_evidence = msg

            if etype == "message":
                role = (event.get("role") or "").lower()
                content = event.get("content") or ""
                if (
                    role == "assistant"
                    and isinstance(content, str)
                    and content
                ):
                    description_parts.append(content)
                # Auth-failure phrases occasionally surface as assistant
                # messages (gemini explaining why it can't help)
                if isinstance(content, str) and any(
                    p in content for p in _AUTH_FAILURE_PHRASES
                ):
                    auth_evidence = content

            if etype in ("tool_use", "tool_result"):
                tool_was_called = True

        # Stderr fallback (mirrors codex_cli pattern PR 16a R2 #1):
        # if gemini bails before stream-json takes effect, the auth
        # phrase surfaces only in stderr.
        if auth_evidence is None and raw.stderr_tail:
            for phrase in _AUTH_FAILURE_PHRASES:
                if phrase in raw.stderr_tail:
                    auth_evidence = raw.stderr_tail.strip()[:500]
                    break

        if auth_evidence is not None:
            raise GeminiCLIAuthError(auth_evidence)

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
        """Pull an error message from a `result` event of non-success status."""
        msg = event.get("message")
        if isinstance(msg, str):
            return msg
        err = event.get("error")
        if isinstance(err, str):
            return err
        if isinstance(err, dict):
            inner = err.get("message")
            if isinstance(inner, str):
                return inner
        return ""
