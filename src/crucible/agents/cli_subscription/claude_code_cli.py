"""ClaudeCodeCLIAdapter — wraps the `claude` CLI binary (M3 PR 16).

NOT to be confused with `crucible/agents/claude_code.py`, which is the
SDK-based ClaudeAgentSDKBackend (uses `claude_agent_sdk` library).
This file is the CLI-subprocess adapter — it invokes the `claude`
binary with `--print --output-format=stream-json` and parses NDJSON
events from stdout.

Reviewer round 1 round 2:
- Spec §3.3: `--print --no-conversation` constraint mode
- Reviewer Q4 schema-version guard: unknown stream-json events
  classified as parse_failure rather than crashing
- Spec §INV-3 belt-and-braces: must NOT pass any flag that re-enables
  the CLI's CodeAct / shell / eval modes
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

from crucible.agents.cli_subscription.base import (
    AdapterRawResult,
    AdapterRunContext,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)

logger = logging.getLogger(__name__)


# stream-json schema versions we know how to parse. Reviewer round 1
# Q4: an unknown schema is classified as parse_failure (not a crash).
KNOWN_STREAM_JSON_SCHEMAS = frozenset({"1", "1.0", "v1"})


class ClaudeCodeCLIAdapter(SubscriptionCLIAdapter):
    """Wrap `claude` CLI in non-interactive print mode."""

    cli_name = "claude-code-cli"
    default_binary_name = "claude"

    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        # Reviewer Q4: --print + --output-format=stream-json is the
        # documented non-conversation mode. We deliberately do NOT
        # pass:
        #   --shell-tool / --bash / --eval / --code-act / etc.
        # If a future CC CLI version ships a CodeAct / REPL flag, it
        # MUST be explicitly forbidden here (spec §INV-3 belt-and-braces).
        return [
            str(self.cli_binary_path),
            "-p", ctx.prompt,
            "--output-format", "stream-json",
            "--verbose",  # required by some CC CLI versions for stream-json
            # NB: do NOT add --shell, --eval, --bash, or any flag that
            # re-enables ad-hoc code execution outside the safe-mode
            # tool surface. See spec §INV-3.
        ]

    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        """Parse NDJSON stream-json output into structured fields.

        Each line is one event. Common types we expect:
          - assistant_message / message_delta : aggregated into description
          - tool_use / tool_result : evidence the agent reached tools
          - error : surfaces format_drift / cli_error

        Per reviewer Q4, an event with an unknown `schema_version` (or
        a malformed JSON line) marks `unknown_schema=True`, signalling
        the compliance harness to classify the trial as `parse_failure`.
        """
        description_parts: list[str] = []
        events: list[dict[str, Any]] = []
        tool_was_called = False
        unknown_schema = False

        for line_no, line in enumerate(raw.stdout.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Plain-text lines may appear if --output-format wasn't
                # respected (CLI version drift). Mark unknown-schema.
                logger.debug(
                    "claude-code-cli: non-JSON output at line %d: %r", line_no, line
                )
                unknown_schema = True
                continue

            if not isinstance(event, dict):
                unknown_schema = True
                continue

            # Schema-version guard (reviewer Q4)
            schema = str(event.get("schema_version", ""))
            if schema and schema not in KNOWN_STREAM_JSON_SCHEMAS:
                unknown_schema = True
                # Don't bail; collect what we can for forensics.

            events.append(event)

            etype = (event.get("type") or "").lower()

            # Message text accumulation
            content = event.get("text") or event.get("content") or ""
            if etype in ("assistant_message", "message", "message_delta") and content:
                if isinstance(content, str):
                    description_parts.append(content)

            # Tool-call evidence (used by tri-state safety detector)
            if etype in ("tool_use", "tool_call", "tool_result"):
                tool_was_called = True

        description = "".join(description_parts).strip()

        # `modified_files` is left empty here — the CLI mutates files
        # directly via its own tool calls; the orchestrator detects
        # changes via mtime snapshot OR scratch-dir copy-back.
        return ParsedAdapterOutput(
            modified_files=[],
            description=description,
            structured_events=events,
            tool_was_called=tool_was_called or None,
            unknown_schema=unknown_schema,
        )
