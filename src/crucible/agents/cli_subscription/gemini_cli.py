"""GeminiCLIAdapter — STUB, gated to PR 16c.

See `codex_cli.py` for the rationale (reviewer round 1 Q1 "B + stubs"
pattern: stubs make factory + adapter registry exhaustive from day 1).
"""

from __future__ import annotations

from typing import Sequence

from crucible.agents.cli_subscription.base import (
    AdapterNotImplementedError,
    AdapterRawResult,
    AdapterRunContext,
    ParsedAdapterOutput,
    SubscriptionCLIAdapter,
)


class GeminiCLIAdapter(SubscriptionCLIAdapter):
    """STUB: gated to PR 16c."""

    cli_name = "gemini-cli"
    default_binary_name = "gemini"

    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        raise AdapterNotImplementedError(
            "GeminiCLIAdapter is a stub gated to PR 16c. "
            "Wire `agent.cli_subscription.adapter: claude-code-cli` instead, "
            "or wait for the Gemini adapter to land."
        )

    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        raise AdapterNotImplementedError(
            "GeminiCLIAdapter is a stub gated to PR 16c."
        )
