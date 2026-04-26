"""CodexCLIAdapter — STUB, gated to PR 16b.

Spec §3.1: experimental backend wrapping the Codex CLI. Reviewer
round 1 Q1 ("B + stubs"): we ship the stub class so the factory
dispatch + adapter registry are exhaustive from day 1, but
`generate_edit` calls fail with `AdapterNotImplementedError` until
the actual implementation lands in PR 16b.

The stub forces the base-class abstraction to handle 3-adapter
shape problems before it ossifies on a single (CC-CLI) consumer.
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


class CodexCLIAdapter(SubscriptionCLIAdapter):
    """STUB: gated to PR 16b. See class-level comment."""

    cli_name = "codex-cli"
    default_binary_name = "codex"

    def build_argv(self, ctx: AdapterRunContext) -> Sequence[str]:
        raise AdapterNotImplementedError(
            "CodexCLIAdapter is a stub gated to PR 16b. "
            "Wire `agent.cli_subscription.adapter: claude-code-cli` instead, "
            "or wait for the Codex adapter to land."
        )

    def parse_output(self, raw: AdapterRawResult) -> ParsedAdapterOutput:
        raise AdapterNotImplementedError(
            "CodexCLIAdapter is a stub gated to PR 16b."
        )
